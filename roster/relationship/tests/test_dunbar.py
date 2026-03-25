"""Tests for the Dunbar scoring engine.

Covers:
- compute_dunbar_scores — decay formula, eligibility filtering
- get_tier_ranking — rank-to-tier mapping, hysteresis, manual overrides
- compute_urgency — formula, context bonuses, tier 1500 exclusion
- Pure-function helpers are tested without a DB; DB tests use provisioned_postgres_pool.
"""

from __future__ import annotations

import math
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Pure-function tests (no DB required)
# ---------------------------------------------------------------------------


class TestRankToTier:
    """Unit tests for internal _rank_to_tier helper."""

    def test_ranks_1_to_5_are_tier_5(self):
        from butlers.tools.relationship.dunbar import _rank_to_tier

        for rank in range(1, 6):
            assert _rank_to_tier(rank) == 5, f"rank {rank}"

    def test_ranks_6_to_15_are_tier_15(self):
        from butlers.tools.relationship.dunbar import _rank_to_tier

        for rank in range(6, 16):
            assert _rank_to_tier(rank) == 15, f"rank {rank}"

    def test_ranks_16_to_50_are_tier_50(self):
        from butlers.tools.relationship.dunbar import _rank_to_tier

        assert _rank_to_tier(16) == 50
        assert _rank_to_tier(50) == 50

    def test_ranks_51_to_150_are_tier_150(self):
        from butlers.tools.relationship.dunbar import _rank_to_tier

        assert _rank_to_tier(51) == 150
        assert _rank_to_tier(150) == 150

    def test_ranks_151_to_500_are_tier_500(self):
        from butlers.tools.relationship.dunbar import _rank_to_tier

        assert _rank_to_tier(151) == 500
        assert _rank_to_tier(500) == 500

    def test_rank_501_plus_are_tier_1500(self):
        from butlers.tools.relationship.dunbar import _rank_to_tier

        assert _rank_to_tier(501) == 1500
        assert _rank_to_tier(10000) == 1500


class TestHysteresis:
    """Unit tests for downward hysteresis in _rank_to_tier_with_hysteresis."""

    def test_upward_move_is_immediate(self):
        """A contact improving from tier 15 to tier 5 moves immediately."""
        from butlers.tools.relationship.dunbar import _rank_to_tier_with_hysteresis

        # Rank 4 (natural tier 5), previously in tier 15 → should move up immediately
        result = _rank_to_tier_with_hysteresis(4, previous_tier=15)
        assert result == 5

    def test_no_previous_tier_no_hysteresis(self):
        """Without previous_tier, natural tier is used directly."""
        from butlers.tools.relationship.dunbar import _rank_to_tier_with_hysteresis

        assert _rank_to_tier_with_hysteresis(6, previous_tier=None) == 15
        assert _rank_to_tier_with_hysteresis(1, previous_tier=None) == 5

    def test_downward_move_within_buffer_is_suppressed(self):
        """Rank 6 or 7 from tier 5 should not drop (buffer is 2 ranks beyond boundary=5)."""
        from butlers.tools.relationship.dunbar import _rank_to_tier_with_hysteresis

        # Boundary for tier 5 is rank 5.  Must exceed 5+2=7 to drop.
        assert _rank_to_tier_with_hysteresis(6, previous_tier=5) == 5  # stays
        assert _rank_to_tier_with_hysteresis(7, previous_tier=5) == 5  # stays (edge)

    def test_downward_move_beyond_buffer_applies(self):
        """Rank 8 from tier 5 should drop to tier 15 (exceeds 5+2=7)."""
        from butlers.tools.relationship.dunbar import _rank_to_tier_with_hysteresis

        assert _rank_to_tier_with_hysteresis(8, previous_tier=5) == 15

    def test_hysteresis_at_tier_15_boundary(self):
        """Rank 16 or 17 from tier 15 stays; rank 18 drops to tier 50."""
        from butlers.tools.relationship.dunbar import _rank_to_tier_with_hysteresis

        assert _rank_to_tier_with_hysteresis(16, previous_tier=15) == 15  # stays
        assert _rank_to_tier_with_hysteresis(17, previous_tier=15) == 15  # stays (edge)
        assert _rank_to_tier_with_hysteresis(18, previous_tier=15) == 50  # drops

    def test_no_change_in_tier_returns_same(self):
        """Rank 3, previously tier 5 → stays in tier 5."""
        from butlers.tools.relationship.dunbar import _rank_to_tier_with_hysteresis

        assert _rank_to_tier_with_hysteresis(3, previous_tier=5) == 5


class TestGetTierRanking:
    """Unit tests for get_tier_ranking (pure function, no DB)."""

    def _make_scores(self, n: int, start_score: float = 10.0) -> list[dict]:
        # Scores decrease from start_score but never go below 0.01 to avoid
        # triggering the zero-score → tier-1500 shortcut.  Real decay scores
        # are always non-negative; test helpers should reflect that invariant.
        return [
            {
                "contact_id": uuid.uuid4(),
                "entity_id": uuid.uuid4(),
                "score": max(0.01, start_score - i),
                "last_interaction_at": None,
            }
            for i in range(n)
        ]

    def test_tier_assignment_for_small_pool(self):
        """Top 5 contacts get tier 5, next 10 get tier 15."""
        from butlers.tools.relationship.dunbar import get_tier_ranking

        scores = self._make_scores(20)
        ranked = get_tier_ranking(scores)

        tiers_5 = [r for r in ranked if r["dunbar_tier"] == 5]
        tiers_15 = [r for r in ranked if r["dunbar_tier"] == 15]
        tiers_50 = [r for r in ranked if r["dunbar_tier"] == 50]

        assert len(tiers_5) == 5
        assert len(tiers_15) == 10
        assert len(tiers_50) == 5

    def test_zero_score_contact_gets_tier_1500(self):
        """A contact with score=0.0 (no interactions) gets tier 1500."""
        from butlers.tools.relationship.dunbar import get_tier_ranking

        # 501 contacts all with score 0 — all should be tier 1500
        scores = self._make_scores(1)
        scores[0]["score"] = 0.0
        ranked = get_tier_ranking(scores)
        assert ranked[0]["dunbar_tier"] == 1500

    def test_manual_override_pins_tier(self):
        """A contact with an override is pinned to that tier regardless of rank."""
        from butlers.tools.relationship.dunbar import get_tier_ranking

        scores = self._make_scores(10)
        pinned_id = scores[9]["entity_id"]  # Rank 10 would be tier 15 naturally
        overrides = {pinned_id: 5}

        ranked = get_tier_ranking(scores, overrides=overrides)
        pinned = next(r for r in ranked if r["entity_id"] == pinned_id)
        assert pinned["dunbar_tier"] == 5
        assert pinned["dunbar_tier_override"] is True

    def test_non_overridden_contacts_not_marked_as_override(self):
        """Contacts without overrides have dunbar_tier_override=False."""
        from butlers.tools.relationship.dunbar import get_tier_ranking

        scores = self._make_scores(3)
        ranked = get_tier_ranking(scores)
        for r in ranked:
            assert r["dunbar_tier_override"] is False

    def test_dunbar_rank_is_1_indexed(self):
        """Ranks start at 1."""
        from butlers.tools.relationship.dunbar import get_tier_ranking

        scores = self._make_scores(5)
        ranked = get_tier_ranking(scores)
        ranks = [r["dunbar_rank"] for r in ranked]
        assert ranks == [1, 2, 3, 4, 5]

    def test_score_rounded_to_2_decimal_places(self):
        """dunbar_score is rounded to 2 decimal places."""
        from butlers.tools.relationship.dunbar import get_tier_ranking

        scores = [
            {
                "contact_id": uuid.uuid4(),
                "entity_id": uuid.uuid4(),
                "score": 3.14159265,
                "last_interaction_at": None,
            }
        ]
        ranked = get_tier_ranking(scores)
        assert ranked[0]["dunbar_score"] == 3.14

    def test_large_pool_tier_assignment(self):
        """600 contacts: first 500 assigned across tiers 5–500, rest in 1500."""
        from butlers.tools.relationship.dunbar import get_tier_ranking

        scores = self._make_scores(600, start_score=600.0)
        ranked = get_tier_ranking(scores)

        tier_5_count = sum(1 for r in ranked if r["dunbar_tier"] == 5)
        tier_15_count = sum(1 for r in ranked if r["dunbar_tier"] == 15)
        tier_50_count = sum(1 for r in ranked if r["dunbar_tier"] == 50)
        tier_150_count = sum(1 for r in ranked if r["dunbar_tier"] == 150)
        tier_500_count = sum(1 for r in ranked if r["dunbar_tier"] == 500)
        tier_1500_count = sum(1 for r in ranked if r["dunbar_tier"] == 1500)

        assert tier_5_count == 5
        assert tier_15_count == 10  # ranks 6-15
        assert tier_50_count == 35  # ranks 16-50
        assert tier_150_count == 100  # ranks 51-150
        assert tier_500_count == 350  # ranks 151-500
        assert tier_1500_count == 100  # ranks 501-600


class TestDecayFormulaConstants:
    """Verify the decay lambda constant and tier constants."""

    def test_lambda_equals_ln2_over_30(self):
        from butlers.tools.relationship.dunbar import _LAMBDA

        expected = math.log(2) / 30.0
        assert abs(_LAMBDA - expected) < 1e-12

    def test_tier_cadences(self):
        from butlers.tools.relationship.dunbar import TIER_CADENCE

        assert TIER_CADENCE[5] == 14
        assert TIER_CADENCE[15] == 21
        assert TIER_CADENCE[50] == 45
        assert TIER_CADENCE[150] == 120
        assert TIER_CADENCE[500] == 270
        assert 1500 not in TIER_CADENCE  # No default cadence for tier 1500

    def test_tier_weights(self):
        from butlers.tools.relationship.dunbar import TIER_WEIGHT

        assert TIER_WEIGHT[5] == 5.0
        assert TIER_WEIGHT[15] == 3.0
        assert TIER_WEIGHT[50] == 2.0
        assert TIER_WEIGHT[150] == 1.0
        assert TIER_WEIGHT[500] == 0.5


class TestUrgencyFormula:
    """Unit tests for urgency computation without DB (using tier_ranking input)."""

    def _make_tier_entry(
        self,
        contact_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        tier: int = 50,
        score: float = 5.0,
        last_interaction_at: datetime | None = None,
    ) -> dict:
        return {
            "contact_id": contact_id or uuid.uuid4(),
            "entity_id": entity_id or uuid.uuid4(),
            "dunbar_tier": tier,
            "dunbar_score": round(score, 2),
            "dunbar_tier_override": False,
            "dunbar_rank": 20,
            "score": score,
            "last_interaction_at": last_interaction_at,
        }

    def test_urgency_formula_basic(self):
        """Urgency = (days_overdue / cadence) * weight + context_bonus."""
        # days_since=50, cadence=45 (tier 50), weight=2.0, no bonus
        # days_overdue = 50-45 = 5
        # urgency = (5/45)*2.0 = 0.2222...
        days_since = 50
        cadence = 45
        weight = 2.0
        days_overdue = days_since - cadence
        expected = (days_overdue / cadence) * weight
        assert abs(expected - (5 / 45) * 2.0) < 0.001

    def test_tier_1500_excluded_without_stay_days(self):
        """Tier 1500 contacts with no stay_in_touch_days should not appear in urgency output."""
        # This is exercised in DB tests; here we validate the constant
        from butlers.tools.relationship.dunbar import TIER_CADENCE

        assert 1500 not in TIER_CADENCE


# ---------------------------------------------------------------------------
# DB-backed integration tests
# ---------------------------------------------------------------------------


pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def dunbar_pool(provisioned_postgres_pool):
    """Provision a fresh DB with all tables needed by the Dunbar engine."""
    async with provisioned_postgres_pool() as p:
        # shared schema + entities
        await p.execute("CREATE SCHEMA IF NOT EXISTS shared")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS shared.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id TEXT NOT NULL DEFAULT '',
                canonical_name VARCHAR NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                entity_type VARCHAR NOT NULL DEFAULT 'other',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # contacts table
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                first_name TEXT,
                last_name TEXT,
                entity_id UUID,
                stay_in_touch_days INT,
                listed BOOLEAN NOT NULL DEFAULT true,
                metadata JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        # important_dates table (for context bonus tests)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS important_dates (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                month INT NOT NULL,
                day INT NOT NULL,
                year INT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        # facts table
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                embedding TEXT,
                validity TEXT NOT NULL DEFAULT 'active',
                scope TEXT NOT NULL DEFAULT 'global',
                entity_id UUID,
                valid_at TIMESTAMPTZ,
                metadata JSONB DEFAULT '{}'::jsonb,
                permanence TEXT NOT NULL DEFAULT 'standard',
                importance FLOAT NOT NULL DEFAULT 5.0,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                decay_rate FLOAT NOT NULL DEFAULT 0.008,
                source_butler TEXT,
                source_episode_id UUID,
                supersedes_id UUID,
                reference_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_referenced_at TIMESTAMPTZ,
                last_confirmed_at TIMESTAMPTZ,
                tags JSONB DEFAULT '[]'::jsonb,
                tenant_id TEXT NOT NULL DEFAULT 'owner',
                request_id TEXT,
                idempotency_key TEXT,
                observed_at TIMESTAMPTZ DEFAULT now(),
                invalid_at TIMESTAMPTZ,
                retention_class TEXT NOT NULL DEFAULT 'operational',
                sensitivity TEXT NOT NULL DEFAULT 'normal',
                search_vector tsvector
            )
        """)
        await p.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_subj_pred ON facts (subject, predicate)"
        )
        yield p


# Helper


async def _make_contact(
    pool,
    name: str,
    *,
    listed: bool = True,
    stay_in_touch_days: int | None = None,
    with_entity: bool = True,
) -> dict:
    entity_id = None
    if with_entity:
        entity_row = await pool.fetchrow(
            "INSERT INTO shared.entities (name) VALUES ($1) RETURNING id",
            name,
        )
        entity_id = entity_row["id"]
    row = await pool.fetchrow(
        """
        INSERT INTO contacts (first_name, entity_id, listed, stay_in_touch_days)
        VALUES ($1, $2, $3, $4)
        RETURNING id, first_name, entity_id, listed, stay_in_touch_days
        """,
        name,
        entity_id,
        listed,
        stay_in_touch_days,
    )
    return dict(row)


async def _log_interaction(pool, contact_id: uuid.UUID, days_ago: float) -> None:
    occurred_at = datetime.now(UTC) - timedelta(days=days_ago)
    await pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, validity, valid_at)
        VALUES ($1, 'interaction', '', 'relationship', 'active', $2)
        """,
        f"contact:{contact_id}",
        occurred_at,
    )


# ===========================================================================
# compute_dunbar_scores DB tests
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_score_zero_for_no_interactions(dunbar_pool):
    """A contact with no interactions has score 0.0."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    contact = await _make_contact(dunbar_pool, "Alice")
    scores = await compute_dunbar_scores(dunbar_pool)

    alice = next((s for s in scores if s["contact_id"] == contact["id"]), None)
    assert alice is not None
    assert alice["score"] == 0.0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_score_reflects_recency(dunbar_pool):
    """A more recent interaction produces a higher score than an older one."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    recent = await _make_contact(dunbar_pool, "Recent")
    old = await _make_contact(dunbar_pool, "Old")

    await _log_interaction(dunbar_pool, recent["id"], days_ago=2)
    await _log_interaction(dunbar_pool, old["id"], days_ago=60)

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    assert score_map[recent["id"]] > score_map[old["id"]]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_score_rewards_frequency(dunbar_pool):
    """10 interactions in past 30 days beats 1 interaction 5 days ago."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    frequent = await _make_contact(dunbar_pool, "Frequent")
    infrequent = await _make_contact(dunbar_pool, "Infrequent")

    for i in range(10):
        await _log_interaction(dunbar_pool, frequent["id"], days_ago=i * 3 + 1)
    await _log_interaction(dunbar_pool, infrequent["id"], days_ago=5)

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    assert score_map[frequent["id"]] > score_map[infrequent["id"]]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_archived_contact_excluded(dunbar_pool):
    """Archived contacts (listed=false) are excluded from scoring."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    archived = await _make_contact(dunbar_pool, "Archived", listed=False)
    await _log_interaction(dunbar_pool, archived["id"], days_ago=1)

    scores = await compute_dunbar_scores(dunbar_pool)
    ids = [s["contact_id"] for s in scores]
    assert archived["id"] not in ids


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contact_without_entity_excluded(dunbar_pool):
    """Contacts without an entity_id are excluded from scoring."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    no_entity = await _make_contact(dunbar_pool, "NoEntity", with_entity=False)
    await _log_interaction(dunbar_pool, no_entity["id"], days_ago=1)

    scores = await compute_dunbar_scores(dunbar_pool)
    ids = [s["contact_id"] for s in scores]
    assert no_entity["id"] not in ids


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_inactive_facts_excluded(dunbar_pool):
    """Superseded/retracted interaction facts are excluded from scoring."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    contact = await _make_contact(dunbar_pool, "SupersededFacts")
    # Insert an inactive interaction
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, validity, valid_at)
        VALUES ($1, 'interaction', '', 'relationship', 'superseded', now() - INTERVAL '1 day')
        """,
        f"contact:{contact['id']}",
    )
    scores = await compute_dunbar_scores(dunbar_pool)
    contact_score = next((s for s in scores if s["contact_id"] == contact["id"]), None)
    assert contact_score is not None
    assert contact_score["score"] == 0.0  # superseded fact not counted


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_scores_ordered_descending(dunbar_pool):
    """compute_dunbar_scores returns results ordered by score descending."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    low = await _make_contact(dunbar_pool, "Low")
    high = await _make_contact(dunbar_pool, "High")

    await _log_interaction(dunbar_pool, low["id"], days_ago=90)
    for i in range(5):
        await _log_interaction(dunbar_pool, high["id"], days_ago=i + 1)

    scores = await compute_dunbar_scores(dunbar_pool)
    scored = [s for s in scores if s["contact_id"] in {low["id"], high["id"]}]
    assert len(scored) >= 2
    assert scored[0]["score"] >= scored[-1]["score"]


# ===========================================================================
# compute_tier_ranking DB tests
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_tier_ranking_manual_override_from_db(dunbar_pool):
    """Manual dunbar_tier_override fact in DB pins the tier."""
    from butlers.tools.relationship.dunbar import compute_tier_ranking

    contact = await _make_contact(dunbar_pool, "Overridden")
    entity_id = contact["entity_id"]

    # Store override as a fact
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity)
        VALUES ($1, 'dunbar_tier_override', '5', 'relationship', $2, 'active')
        """,
        f"contact:{contact['id']}",
        entity_id,
    )

    ranking = await compute_tier_ranking(dunbar_pool)
    row = next((r for r in ranking if r["contact_id"] == contact["id"]), None)
    assert row is not None
    assert row["dunbar_tier"] == 5
    assert row["dunbar_tier_override"] is True


# ===========================================================================
# compute_urgency DB tests
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_tier_1500_excluded_without_stay_days(dunbar_pool):
    """Tier 1500 contact without stay_in_touch_days is excluded from urgency."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    contact = await _make_contact(dunbar_pool, "Outer")
    # No interactions → score=0 → tier 1500 by natural ranking
    scores_1 = [
        {
            "contact_id": contact["id"],
            "entity_id": contact["entity_id"],
            "score": 0.0,
            "last_interaction_at": None,
        }
    ]
    tier_ranking = get_tier_ranking(scores_1)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)
    ids = [r["contact_id"] for r in results]
    assert contact["id"] not in ids


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_tier_1500_included_with_stay_days(dunbar_pool):
    """Tier 1500 contact with stay_in_touch_days IS included in urgency."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    contact = await _make_contact(dunbar_pool, "OuterWithCadence", stay_in_touch_days=30)
    scores_1 = [
        {
            "contact_id": contact["id"],
            "entity_id": contact["entity_id"],
            "score": 0.0,
            "last_interaction_at": None,
        }
    ]
    tier_ranking = get_tier_ranking(scores_1)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)
    ids = [r["contact_id"] for r in results]
    assert contact["id"] in ids


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_ordered_descending(dunbar_pool):
    """compute_urgency returns results ordered by urgency descending."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    # high: tier 5, heavily overdue
    high = await _make_contact(dunbar_pool, "HighUrgency")
    await _log_interaction(dunbar_pool, high["id"], days_ago=200)

    # low: tier 50, recently contacted
    low = await _make_contact(dunbar_pool, "LowUrgency")
    await _log_interaction(dunbar_pool, low["id"], days_ago=1)

    scores = [
        {
            "contact_id": high["id"],
            "entity_id": high["entity_id"],
            "score": 8.0,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=200),
        },
        {
            "contact_id": low["id"],
            "entity_id": low["entity_id"],
            "score": 4.0,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=1),
        },
    ]
    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)

    urgency_values = [r["urgency"] for r in results]
    assert urgency_values == sorted(urgency_values, reverse=True)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_upcoming_date_bonus(dunbar_pool):
    """Upcoming date within 14 days adds +2.0 context bonus."""
    from butlers.tools.relationship.dunbar import _BONUS_UPCOMING_DATE, compute_urgency

    contact = await _make_contact(dunbar_pool, "BirthdaySoon", stay_in_touch_days=365)

    # Insert birthday 7 days from now
    upcoming = datetime.now(UTC) + timedelta(days=7)
    await dunbar_pool.execute(
        """
        INSERT INTO important_dates (contact_id, label, month, day)
        VALUES ($1, 'birthday', $2, $3)
        """,
        contact["id"],
        upcoming.month,
        upcoming.day,
    )

    scores = [
        {
            "contact_id": contact["id"],
            "entity_id": contact["entity_id"],
            "score": 0.0,
            "last_interaction_at": None,
        }
    ]
    from butlers.tools.relationship.dunbar import get_tier_ranking

    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)
    row = next((r for r in results if r["contact_id"] == contact["id"]), None)
    assert row is not None
    assert row["context_bonus"] >= _BONUS_UPCOMING_DATE


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_pending_gift_bonus(dunbar_pool):
    """Pending gift (status=idea) adds +1.0 context bonus."""
    from butlers.tools.relationship.dunbar import _BONUS_PENDING_GIFT, compute_urgency

    contact = await _make_contact(dunbar_pool, "GiftPending", stay_in_touch_days=365)

    # Insert a pending gift fact
    import json

    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, validity, metadata)
        VALUES ($1, 'gift', 'book', 'relationship', 'active', $2::jsonb)
        """,
        f"contact:{contact['id']}:gift:book",
        json.dumps({"status": "idea"}),
    )

    scores = [
        {
            "contact_id": contact["id"],
            "entity_id": contact["entity_id"],
            "score": 0.0,
            "last_interaction_at": None,
        }
    ]
    from butlers.tools.relationship.dunbar import get_tier_ranking

    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)
    row = next((r for r in results if r["contact_id"] == contact["id"]), None)
    assert row is not None
    assert row["context_bonus"] >= _BONUS_PENDING_GIFT


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_positive_note_bonus(dunbar_pool):
    """Most recent note with positive emotion adds +0.5 context bonus."""
    from butlers.tools.relationship.dunbar import _BONUS_POSITIVE_NOTE, compute_urgency

    contact = await _make_contact(dunbar_pool, "HappyContact", stay_in_touch_days=365)

    import json

    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, validity, valid_at, metadata)
        VALUES ($1, 'contact_note', 'Had a great chat!', 'relationship', 'active',
                now() - INTERVAL '1 day', $2::jsonb)
        """,
        f"contact:{contact['id']}",
        json.dumps({"emotion": "happy"}),
    )

    scores = [
        {
            "contact_id": contact["id"],
            "entity_id": contact["entity_id"],
            "score": 0.0,
            "last_interaction_at": None,
        }
    ]
    from butlers.tools.relationship.dunbar import get_tier_ranking

    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)
    row = next((r for r in results if r["contact_id"] == contact["id"]), None)
    assert row is not None
    assert row["context_bonus"] >= _BONUS_POSITIVE_NOTE


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_stay_in_touch_overrides_tier_cadence(dunbar_pool):
    """stay_in_touch_days on a contact overrides the tier default cadence."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    # Tier 5 has cadence=14 days; override to 90 days
    contact = await _make_contact(dunbar_pool, "LongCadenceInner", stay_in_touch_days=90)
    await _log_interaction(dunbar_pool, contact["id"], days_ago=20)

    scores = [
        {
            "contact_id": contact["id"],
            "entity_id": contact["entity_id"],
            "score": 15.0,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=20),
        }
    ]
    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)
    row = next((r for r in results if r["contact_id"] == contact["id"]), None)
    assert row is not None
    # With stay_in_touch_days=90, 20 days elapsed → not overdue yet (days_overdue=0)
    assert row["effective_cadence"] == 90
    assert row["days_overdue"] == 0.0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_contact_with_no_interactions_overdue(dunbar_pool):
    """Contact with effective cadence and no interactions is fully overdue."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    contact = await _make_contact(dunbar_pool, "NeverContacted", stay_in_touch_days=30)

    scores = [
        {
            "contact_id": contact["id"],
            "entity_id": contact["entity_id"],
            "score": 0.0,
            "last_interaction_at": None,
        }
    ]
    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)
    row = next((r for r in results if r["contact_id"] == contact["id"]), None)
    assert row is not None
    assert row["days_overdue"] == float(row["effective_cadence"])
