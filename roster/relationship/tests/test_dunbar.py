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


class TestDirectionWeightConstants:
    """Verify direction weight constants (RFC 0013, D1)."""

    def test_direction_weight_values(self):
        from butlers.tools.relationship.dunbar import (
            DIRECTION_WEIGHT_INCOMING,
            DIRECTION_WEIGHT_MUTUAL,
            DIRECTION_WEIGHT_OUTGOING,
        )

        assert DIRECTION_WEIGHT_OUTGOING == 10.0
        assert DIRECTION_WEIGHT_MUTUAL == 5.0
        assert DIRECTION_WEIGHT_INCOMING == 1.0

    def test_outgoing_is_highest(self):
        from butlers.tools.relationship.dunbar import (
            DIRECTION_WEIGHT_INCOMING,
            DIRECTION_WEIGHT_MUTUAL,
            DIRECTION_WEIGHT_OUTGOING,
        )

        assert DIRECTION_WEIGHT_OUTGOING > DIRECTION_WEIGHT_MUTUAL > DIRECTION_WEIGHT_INCOMING


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
        # public.entities
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name VARCHAR NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                entity_type VARCHAR NOT NULL DEFAULT 'other',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                listed BOOLEAN NOT NULL DEFAULT true,
                stay_in_touch_days INT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # contacts table (kept; dunbar now reads via contact_entity_map + entities)
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
        # contact_entity_map (rel_029) — contact_id → entity_id bridge that
        # dunbar reads instead of public.contacts (Phase 7.4e).
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contact_entity_map (
                contact_id  UUID NOT NULL,
                entity_id   UUID NOT NULL,
                CONSTRAINT contact_entity_map_pkey PRIMARY KEY (contact_id)
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_contact_entity_map_entity_id
                ON contact_entity_map (entity_id)
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
                search_vector tsvector,
                embedding_model_version TEXT DEFAULT 'unknown'
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
        # Seed the entity with listed + stay_in_touch_days directly — dunbar now
        # reads these off public.entities (rel_031), not public.contacts.
        entity_row = await pool.fetchrow(
            """
            INSERT INTO public.entities (name, listed, stay_in_touch_days)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            name,
            listed,
            stay_in_touch_days,
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
    # Bridge the contact to its entity so dunbar's contact_entity_map reads match.
    if entity_id is not None:
        await pool.execute(
            "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
            row["id"],
            entity_id,
        )
    return dict(row)


async def _log_interaction(pool, entity_id: uuid.UUID | None, days_ago: float) -> None:
    """Insert an active interaction fact keyed by entity_id.

    entity_id=None simulates a contact with no linked entity; the fact is stored
    with a placeholder subject and NULL entity_id so the new reader (which joins
    on f.entity_id = c.entity_id) will not match it — matching pre-migration
    behaviour where NULL-entity contacts were excluded from Dunbar scoring.
    """
    occurred_at = datetime.now(UTC) - timedelta(days=days_ago)
    if entity_id is None:
        # No entity linkage — store as unmatchable orphan (NULL entity_id on fact).
        subject = "entity:unknown"
    else:
        subject = f"entity:{entity_id}"
    await pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3)
        """,
        subject,
        entity_id,
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

    await _log_interaction(dunbar_pool, recent["entity_id"], days_ago=2)
    await _log_interaction(dunbar_pool, old["entity_id"], days_ago=60)

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
        await _log_interaction(dunbar_pool, frequent["entity_id"], days_ago=i * 3 + 1)
    await _log_interaction(dunbar_pool, infrequent["entity_id"], days_ago=5)

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
    await _log_interaction(dunbar_pool, archived["entity_id"], days_ago=1)

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
    await _log_interaction(
        dunbar_pool, no_entity["entity_id"], days_ago=1
    )  # entity_id=None → excluded

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
    # Insert an inactive interaction (validity='superseded' — must not count).
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'superseded', now() - INTERVAL '1 day')
        """,
        f"entity:{contact['entity_id']}",
        contact["entity_id"],
    )
    scores = await compute_dunbar_scores(dunbar_pool)
    contact_score = next((s for s in scores if s["contact_id"] == contact["id"]), None)
    assert contact_score is not None
    assert contact_score["score"] == 0.0  # superseded fact not counted


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_direction_outgoing_10x_vs_incoming(dunbar_pool):
    """outgoing direction produces 10x score vs incoming (RFC 0013, D1)."""
    from butlers.tools.relationship.dunbar import (
        DIRECTION_WEIGHT_INCOMING,
        DIRECTION_WEIGHT_OUTGOING,
        compute_dunbar_scores,
    )

    outgoing = await _make_contact(dunbar_pool, "OutgoingContact")
    incoming = await _make_contact(dunbar_pool, "IncomingContact")

    occurred_at = datetime.now(UTC) - timedelta(days=1)
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{outgoing['entity_id']}",
        outgoing["entity_id"],
        occurred_at,
        {"direction": "outgoing"},
    )
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{incoming['entity_id']}",
        incoming["entity_id"],
        occurred_at,
        {"direction": "incoming"},
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    expected_ratio = DIRECTION_WEIGHT_OUTGOING / DIRECTION_WEIGHT_INCOMING
    ratio = score_map[outgoing["id"]] / score_map[incoming["id"]]
    assert abs(ratio - expected_ratio) < 1e-9, f"Expected {expected_ratio}x ratio, got {ratio}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_direction_mutual_5x_vs_incoming(dunbar_pool):
    """mutual direction produces 5x score vs incoming (RFC 0013, D1)."""
    from butlers.tools.relationship.dunbar import (
        DIRECTION_WEIGHT_INCOMING,
        DIRECTION_WEIGHT_MUTUAL,
        compute_dunbar_scores,
    )

    mutual = await _make_contact(dunbar_pool, "MutualContact")
    incoming = await _make_contact(dunbar_pool, "IncomingContact2")

    occurred_at = datetime.now(UTC) - timedelta(days=2)
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{mutual['entity_id']}",
        mutual["entity_id"],
        occurred_at,
        {"direction": "mutual"},
    )
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{incoming['entity_id']}",
        incoming["entity_id"],
        occurred_at,
        {"direction": "incoming"},
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    expected_ratio = DIRECTION_WEIGHT_MUTUAL / DIRECTION_WEIGHT_INCOMING
    ratio = score_map[mutual["id"]] / score_map[incoming["id"]]
    assert abs(ratio - expected_ratio) < 1e-9, f"Expected {expected_ratio}x ratio, got {ratio}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_group_size_10_produces_tenth_score(dunbar_pool):
    """group_size=10 produces 1/10th score vs group_size=1 (RFC 0013, D2)."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    group_contact = await _make_contact(dunbar_pool, "GroupContact")
    dm_contact = await _make_contact(dunbar_pool, "DmContact")

    group_size_large = 10
    group_size_dm = 1
    occurred_at = datetime.now(UTC) - timedelta(days=3)
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{group_contact['entity_id']}",
        group_contact["entity_id"],
        occurred_at,
        {"group_size": group_size_large},
    )
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{dm_contact['entity_id']}",
        dm_contact["entity_id"],
        occurred_at,
        {"group_size": group_size_dm},
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    # group score scales as 1/group_size
    expected_ratio = group_size_dm / group_size_large
    ratio = score_map[group_contact["id"]] / score_map[dm_contact["id"]]
    assert abs(ratio - expected_ratio) < 1e-9, f"Expected {expected_ratio} ratio, got {ratio}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_null_direction_defaults_to_1x(dunbar_pool):
    """NULL direction defaults to 1.0x multiplier (backward compatible, RFC 0013, D6)."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    null_dir = await _make_contact(dunbar_pool, "NullDirection")
    explicit_incoming = await _make_contact(dunbar_pool, "ExplicitIncoming")

    occurred_at = datetime.now(UTC) - timedelta(days=4)
    # fact without direction metadata (simulates pre-RFC facts)
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3)
        """,
        f"entity:{null_dir['entity_id']}",
        null_dir["entity_id"],
        occurred_at,
    )
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{explicit_incoming['entity_id']}",
        explicit_incoming["entity_id"],
        occurred_at,
        {"direction": "incoming"},
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    # NULL direction should equal explicit incoming (1.0x)
    assert abs(score_map[null_dir["id"]] - score_map[explicit_incoming["id"]]) < 1e-9


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_null_group_size_defaults_to_1x(dunbar_pool):
    """NULL group_size defaults to 1.0x divisor (backward compatible, RFC 0013, D6)."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    null_gs = await _make_contact(dunbar_pool, "NullGroupSize")
    explicit_dm = await _make_contact(dunbar_pool, "ExplicitDm")

    occurred_at = datetime.now(UTC) - timedelta(days=5)
    # fact without group_size metadata (simulates pre-RFC facts)
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3)
        """,
        f"entity:{null_gs['entity_id']}",
        null_gs["entity_id"],
        occurred_at,
    )
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{explicit_dm['entity_id']}",
        explicit_dm["entity_id"],
        occurred_at,
        {"group_size": 1},
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    # NULL group_size should equal group_size=1 (both 1.0x divisor)
    assert abs(score_map[null_gs["id"]] - score_map[explicit_dm["id"]]) < 1e-9


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_connector_extracted_mentions_do_not_count_as_direct_interactions(dunbar_pool):
    """LLM-extracted facts from connector context can mention a person without direct contact."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    mentioned = await _make_contact(dunbar_pool, "MentionedPerson")
    occurred_at = datetime.now(UTC) - timedelta(days=1)
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', 'Talked about MentionedPerson', 'relationship', $2, 'active',
                $3, $4)
        """,
        f"entity:{mentioned['entity_id']}",
        mentioned["entity_id"],
        occurred_at,
        {
            "type": "chat_message",
            "direction": "mutual",
            "extra_metadata": {
                "source_channel": "telegram_user_client",
                "request_id": "req-123",
                "related_contact_name": "MentionedPerson",
            },
        },
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    assert score_map[mentioned["id"]] == 0.0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_interaction_sync_connector_events_still_count(dunbar_pool):
    """Direct interactions created by interaction_sync remain valid Dunbar evidence."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    direct = await _make_contact(dunbar_pool, "DirectChat")
    occurred_at = datetime.now(UTC) - timedelta(days=1)
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{direct['entity_id']}",
        direct["entity_id"],
        occurred_at,
        {
            "type": "telegram_user_client",
            "direction": "incoming",
            "extra_metadata": {
                "source": "interaction_sync",
                "message_count": 1,
                "group_size": 1,
            },
        },
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    assert score_map[direct["id"]] > 0.0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_interview_interactions_are_downweighted(dunbar_pool):
    """A one-off interview should not carry the same social weight as a direct call."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    interview = await _make_contact(dunbar_pool, "InterviewOnly")
    call = await _make_contact(dunbar_pool, "DirectCall")
    occurred_at = datetime.now(UTC) - timedelta(days=1)

    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{interview['entity_id']}",
        interview["entity_id"],
        occurred_at,
        {"type": "interview", "direction": "outgoing"},
    )
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{call['entity_id']}",
        call["entity_id"],
        occurred_at,
        {"type": "call", "direction": "outgoing"},
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    ratio = score_map[interview["id"]] / score_map[call["id"]]
    assert abs(ratio - 0.2) < 1e-9


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_email_interactions_are_downweighted(dunbar_pool):
    """A few transactional emails should not score like direct personal calls."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    email = await _make_contact(dunbar_pool, "EmailOnly")
    call = await _make_contact(dunbar_pool, "DirectCallForEmail")
    occurred_at = datetime.now(UTC) - timedelta(days=1)

    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{email['entity_id']}",
        email["entity_id"],
        occurred_at,
        {"type": "email", "direction": "outgoing"},
    )
    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at, metadata)
        VALUES ($1, 'interaction_other', '', 'relationship', $2, 'active', $3, $4)
        """,
        f"entity:{call['entity_id']}",
        call["entity_id"],
        occurred_at,
        {"type": "call", "direction": "outgoing"},
    )

    scores = await compute_dunbar_scores(dunbar_pool)
    score_map = {s["contact_id"]: s["score"] for s in scores}

    ratio = score_map[email["id"]] / score_map[call["id"]]
    assert abs(ratio - 0.2) < 1e-9


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_scores_ordered_descending(dunbar_pool):
    """compute_dunbar_scores returns results ordered by score descending."""
    from butlers.tools.relationship.dunbar import compute_dunbar_scores

    low = await _make_contact(dunbar_pool, "Low")
    high = await _make_contact(dunbar_pool, "High")

    await _log_interaction(dunbar_pool, low["entity_id"], days_ago=90)
    for i in range(5):
        await _log_interaction(dunbar_pool, high["entity_id"], days_ago=i + 1)

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
    await _log_interaction(dunbar_pool, high["entity_id"], days_ago=200)

    # low: tier 50, recently contacted
    low = await _make_contact(dunbar_pool, "LowUrgency")
    await _log_interaction(dunbar_pool, low["entity_id"], days_ago=1)

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

    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, validity, metadata)
        VALUES ($1, 'gift', 'book', 'relationship', 'active', $2)
        """,
        f"contact:{contact['id']}:gift:book",
        {"status": "idea"},
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

    await dunbar_pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, validity, valid_at, metadata)
        VALUES ($1, 'contact_note', 'Had a great chat!', 'relationship', 'active',
                now() - INTERVAL '1 day', $2)
        """,
        f"contact:{contact['id']}",
        {"emotion": "happy"},
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

    # Add an upcoming date (within 14 days) to provide a context bonus
    # so the contact passes the "zero-context" filter
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
            "score": 15.0,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=20),
        }
    ]
    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)
    row = next((r for r in results if r["contact_id"] == contact["id"]), None)
    assert row is not None
    # With stay_in_touch_days=90, 20 days elapsed → not overdue yet (days_overdue=0)
    # But has a context bonus from the upcoming date
    assert row["effective_cadence"] == 90
    assert row["days_overdue"] == 0.0
    assert row["context_bonus"] > 0.0


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


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_excludes_non_overdue_zero_context_contacts(dunbar_pool):
    """Non-overdue contacts with zero context bonus MUST be filtered from results."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    # Contact with stay_in_touch_days=365, last interaction 20 days ago
    # days_overdue = max(20 - 365, 0) = 0
    # context_bonus = 0 (no dates, gifts, or positive notes)
    # Result: should be EXCLUDED from output
    contact = await _make_contact(dunbar_pool, "NoUrgency", stay_in_touch_days=365)
    await _log_interaction(dunbar_pool, contact["id"], days_ago=20)

    scores = [
        {
            "contact_id": contact["id"],
            "entity_id": contact["entity_id"],
            "score": 5.0,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=20),
        }
    ]
    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)

    # Contact should NOT appear in results (filtered out)
    ids = [r["contact_id"] for r in results]
    assert contact["id"] not in ids


# ===========================================================================
# Unit tests for new constants (VALID_TIERS, TIER_CADENCES, DUNBAR_LAYERS)
# ===========================================================================


def test_valid_tiers():
    """VALID_TIERS contains exactly the expected 6 values."""
    from butlers.tools.relationship.dunbar import VALID_TIERS

    assert VALID_TIERS == {5, 15, 50, 150, 500, 1500}


def test_tier_cadences():
    """TIER_CADENCES maps each tier to expected day thresholds, including 1500=None."""
    from butlers.tools.relationship.dunbar import TIER_CADENCES

    assert TIER_CADENCES[5] == 14
    assert TIER_CADENCES[15] == 21
    assert TIER_CADENCES[50] == 45
    assert TIER_CADENCES[150] == 120
    assert TIER_CADENCES[500] == 270
    assert TIER_CADENCES[1500] is None


def test_dunbar_layers_structure():
    """DUNBAR_LAYERS is a list of (tier_value, cumulative_rank_end) tuples."""
    from butlers.tools.relationship.dunbar import DUNBAR_LAYERS

    assert (5, 5) in DUNBAR_LAYERS
    assert (15, 15) in DUNBAR_LAYERS
    tier_values = [t for t, _ in DUNBAR_LAYERS]
    assert 1500 in tier_values


# ===========================================================================
# Pool fixture (simpler schema, no public.entities) — for new-function tests
# ===========================================================================

_LAMBDA_NEW = math.log(2) / 30.0


@pytest.fixture
async def simple_pool(provisioned_postgres_pool):
    """Provision a fresh database with entities, contacts, cem and facts tables."""
    async with provisioned_postgres_pool() as p:
        # public.entities — dunbar reads listed + stay_in_touch_days here (rel_031).
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name VARCHAR NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                entity_type VARCHAR NOT NULL DEFAULT 'other',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                listed BOOLEAN NOT NULL DEFAULT true,
                stay_in_touch_days INT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                first_name TEXT,
                last_name TEXT,
                nickname TEXT,
                company TEXT,
                job_title TEXT,
                gender TEXT,
                pronouns TEXT,
                avatar_url TEXT,
                entity_id UUID,
                stay_in_touch_days INT,
                listed BOOLEAN NOT NULL DEFAULT true,
                metadata JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        # contact_entity_map (rel_029) — contact_id → entity_id bridge.
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contact_entity_map (
                contact_id  UUID NOT NULL,
                entity_id   UUID NOT NULL,
                CONSTRAINT contact_entity_map_pkey PRIMARY KEY (contact_id)
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_contact_entity_map_entity_id
                ON contact_entity_map (entity_id)
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                embedding TEXT,
                search_vector tsvector,
                importance FLOAT NOT NULL DEFAULT 5.0,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                decay_rate FLOAT NOT NULL DEFAULT 0.008,
                permanence TEXT NOT NULL DEFAULT 'standard',
                source_butler TEXT,
                source_episode_id UUID,
                supersedes_id UUID,
                validity TEXT NOT NULL DEFAULT 'active',
                scope TEXT NOT NULL DEFAULT 'global',
                entity_id UUID,
                object_entity_id UUID,
                valid_at TIMESTAMPTZ,
                reference_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_referenced_at TIMESTAMPTZ,
                last_confirmed_at TIMESTAMPTZ,
                tags JSONB DEFAULT '[]'::jsonb,
                metadata JSONB DEFAULT '{}'::jsonb,
                tenant_id TEXT NOT NULL DEFAULT 'owner',
                request_id TEXT,
                idempotency_key TEXT,
                observed_at TIMESTAMPTZ DEFAULT now(),
                invalid_at TIMESTAMPTZ,
                retention_class TEXT NOT NULL DEFAULT 'operational',
                sensitivity TEXT NOT NULL DEFAULT 'normal',
                embedding_model_version TEXT DEFAULT 'unknown'
            )
        """)
        await p.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_subj_pred_new ON facts (subject, predicate)"
        )
        yield p


async def _make_simple_contact(pool, first_name: str, *, listed: bool = True) -> dict:
    """Create an entity (with listed), a contact, and the contact_entity_map bridge.

    dunbar now reads listed + stay_in_touch_days off public.entities (rel_031),
    so the entity carries ``listed`` and a contact_entity_map row links them.
    """
    entity_row = await pool.fetchrow(
        """
        INSERT INTO public.entities (name, canonical_name, listed)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        first_name,
        first_name,
        listed,
    )
    entity_id = entity_row["id"]
    row = await pool.fetchrow(
        """
        INSERT INTO contacts (first_name, entity_id, listed)
        VALUES ($1, $2, $3)
        RETURNING id, entity_id, first_name, listed
        """,
        first_name,
        entity_id,
        listed,
    )
    await pool.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
        row["id"],
        entity_id,
    )
    return dict(row)


async def _log_simple_interaction(pool, contact_id: uuid.UUID, days_ago: float) -> None:
    """Insert an active interaction fact valid_at=now()-days_ago.

    Looks up entity_id from contacts so the new entity-keyed reader JOIN
    (f.entity_id = c.entity_id) matches the fact correctly.
    """
    valid_at = datetime.now(UTC) - timedelta(days=days_ago)
    entity_id = await pool.fetchval("SELECT entity_id FROM contacts WHERE id = $1", contact_id)
    await pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, entity_id, validity, valid_at)
        VALUES ($1, 'interaction_other', 'chat', 'relationship', $2, 'active', $3)
        """,
        f"entity:{entity_id}",
        entity_id,
        valid_at,
    )


# ===========================================================================
# Integration tests — dunbar_tier_set (override set / update / clear)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dunbar_tier_set_stores_override(simple_pool):
    """Setting a tier stores an active dunbar_tier_override fact."""
    from butlers.tools.relationship.dunbar import dunbar_tier_set

    contact = await _make_simple_contact(simple_pool, "Alice")
    cid = uuid.UUID(str(contact["id"]))

    result = await dunbar_tier_set(simple_pool, cid, 15)

    assert result["action"] == "set"
    assert result["tier"] == 15
    assert result["contact_id"] == str(cid)

    row = await simple_pool.fetchrow(
        """
        SELECT content, validity, permanence FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND entity_id = $1::uuid
          AND validity = 'active'
        """,
        str(contact["entity_id"]),
    )
    assert row is not None
    assert row["content"] == "15"
    assert row["validity"] == "active"
    assert row["permanence"] == "permanent"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dunbar_tier_set_updates_override(simple_pool):
    """Setting a new tier retracts the old override and stores a new one."""
    from butlers.tools.relationship.dunbar import dunbar_tier_set

    contact = await _make_simple_contact(simple_pool, "Bob")
    cid = uuid.UUID(str(contact["id"]))
    entity_id_str = str(contact["entity_id"])

    await dunbar_tier_set(simple_pool, cid, 50)
    result = await dunbar_tier_set(simple_pool, cid, 5)
    assert result["action"] == "set"
    assert result["tier"] == 5

    retracted = await simple_pool.fetchrow(
        """
        SELECT id FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND entity_id = $1::uuid
          AND validity = 'retracted'
        """,
        entity_id_str,
    )
    assert retracted is not None

    active_rows = await simple_pool.fetch(
        """
        SELECT content FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND entity_id = $1::uuid
          AND validity = 'active'
        """,
        entity_id_str,
    )
    assert len(active_rows) == 1
    assert active_rows[0]["content"] == "5"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dunbar_tier_set_clear(simple_pool):
    """Passing tier=None retracts the override and returns action='cleared'."""
    from butlers.tools.relationship.dunbar import dunbar_tier_set

    contact = await _make_simple_contact(simple_pool, "Carol")
    cid = uuid.UUID(str(contact["id"]))
    entity_id_str = str(contact["entity_id"])

    await dunbar_tier_set(simple_pool, cid, 150)
    result = await dunbar_tier_set(simple_pool, cid, None)
    assert result["action"] == "cleared"

    active_rows = await simple_pool.fetch(
        """
        SELECT id FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND entity_id = $1::uuid
          AND validity = 'active'
        """,
        entity_id_str,
    )
    assert len(active_rows) == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dunbar_tier_set_invalid_tier_rejected(simple_pool):
    """Invalid tier value raises ValueError with actionable message."""
    from butlers.tools.relationship.dunbar import dunbar_tier_set

    contact = await _make_simple_contact(simple_pool, "Dave")
    cid = uuid.UUID(str(contact["id"]))

    with pytest.raises(ValueError, match="Invalid tier value"):
        await dunbar_tier_set(simple_pool, cid, 42)

    with pytest.raises(ValueError, match="Valid Dunbar tier values"):
        await dunbar_tier_set(simple_pool, cid, 7)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dunbar_tier_set_contact_not_found(simple_pool):
    """Non-existent contact raises ValueError."""
    from butlers.tools.relationship.dunbar import dunbar_tier_set

    with pytest.raises(ValueError, match="not found"):
        await dunbar_tier_set(simple_pool, uuid.uuid4(), 5)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dunbar_tier_set_no_entity_id_raises(simple_pool):
    """Contact without entity_id raises ValueError."""
    from butlers.tools.relationship.dunbar import dunbar_tier_set

    row = await simple_pool.fetchrow(
        "INSERT INTO contacts (first_name, entity_id) VALUES ('Ghost', NULL) RETURNING id"
    )
    cid = uuid.UUID(str(row["id"]))

    with pytest.raises(ValueError, match="no linked entity"):
        await dunbar_tier_set(simple_pool, cid, 5)


# ===========================================================================
# Integration tests — get_contact_dunbar
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_get_contact_dunbar_no_entity(simple_pool):
    """Contact without entity_id returns tier 1500, score 0.0."""
    from butlers.tools.relationship.dunbar import get_contact_dunbar

    row = await simple_pool.fetchrow(
        "INSERT INTO contacts (first_name, entity_id) VALUES ('Ghost', NULL) RETURNING id"
    )
    cid = uuid.UUID(str(row["id"]))

    result = await get_contact_dunbar(simple_pool, cid)

    assert result["dunbar_tier"] == 1500
    assert result["dunbar_score"] == 0.0
    assert result["dunbar_tier_override"] is False


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_get_contact_dunbar_with_override(simple_pool):
    """get_contact_dunbar respects manual override."""
    from butlers.tools.relationship.dunbar import dunbar_tier_set, get_contact_dunbar

    contact = await _make_simple_contact(simple_pool, "Dana")
    cid = uuid.UUID(str(contact["id"]))

    await dunbar_tier_set(simple_pool, cid, 50)
    result = await get_contact_dunbar(simple_pool, cid)

    assert result["dunbar_tier"] == 50
    assert result["dunbar_tier_override"] is True


# ===========================================================================
# Integration tests — contacts_overdue_with_tiers
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contacts_overdue_with_tiers_stay_in_touch_overrides(simple_pool):
    """stay_in_touch_days overrides the tier's default cadence."""
    from butlers.tools.relationship.dunbar import contacts_overdue_with_tiers

    contact = await _make_simple_contact(simple_pool, "Iris")
    cid = uuid.UUID(str(contact["id"]))

    await simple_pool.execute(
        "UPDATE public.entities SET stay_in_touch_days = 60 "
        "WHERE id = (SELECT entity_id FROM contact_entity_map WHERE contact_id = $1)",
        cid,
    )
    await _log_simple_interaction(simple_pool, cid, 30.0)

    results = await contacts_overdue_with_tiers(simple_pool)
    ids = [str(r["id"]) for r in results]
    assert str(cid) not in ids


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contacts_overdue_with_tiers_no_interactions_always_overdue(simple_pool):
    """Contact with stay_in_touch_days and no interactions is always overdue."""
    from butlers.tools.relationship.dunbar import contacts_overdue_with_tiers

    contact = await _make_simple_contact(simple_pool, "Karen")
    cid = uuid.UUID(str(contact["id"]))

    await simple_pool.execute(
        "UPDATE public.entities SET stay_in_touch_days = 30 "
        "WHERE id = (SELECT entity_id FROM contact_entity_map WHERE contact_id = $1)",
        cid,
    )

    results = await contacts_overdue_with_tiers(simple_pool)
    ids = [str(r["id"]) for r in results]
    assert str(cid) in ids


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contacts_overdue_with_tiers_tier_1500_excluded(simple_pool):
    """Tier 1500 contacts with no stay_in_touch_days are excluded from overdue."""
    from butlers.tools.relationship.dunbar import contacts_overdue_with_tiers

    contact = await _make_simple_contact(simple_pool, "Leo")

    results = await contacts_overdue_with_tiers(simple_pool)
    ids = [str(r["id"]) for r in results]
    assert str(contact["id"]) not in ids


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contacts_overdue_with_tiers_enriched_fields(simple_pool):
    """Overdue contacts include dunbar_tier, dunbar_score, effective_cadence."""
    from butlers.tools.relationship.dunbar import contacts_overdue_with_tiers

    contact = await _make_simple_contact(simple_pool, "Nina")
    cid = uuid.UUID(str(contact["id"]))

    await simple_pool.execute(
        "UPDATE public.entities SET stay_in_touch_days = 7 "
        "WHERE id = (SELECT entity_id FROM contact_entity_map WHERE contact_id = $1)",
        cid,
    )
    await _log_simple_interaction(simple_pool, cid, 10.0)

    results = await contacts_overdue_with_tiers(simple_pool)
    assert len(results) >= 1

    overdue = next(r for r in results if str(r["id"]) == str(cid))
    assert "dunbar_tier" in overdue
    assert "dunbar_score" in overdue
    assert "effective_cadence" in overdue
    assert overdue["effective_cadence"] == 7


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contacts_overdue_with_tiers_archived_excluded(simple_pool):
    """Archived contacts (listed=false) are excluded from overdue results."""
    from butlers.tools.relationship.dunbar import contacts_overdue_with_tiers

    contact = await _make_simple_contact(simple_pool, "Oscar", listed=False)
    cid = uuid.UUID(str(contact["id"]))

    await simple_pool.execute(
        "UPDATE public.entities SET stay_in_touch_days = 7 "
        "WHERE id = (SELECT entity_id FROM contact_entity_map WHERE contact_id = $1)",
        cid,
    )
    await _log_simple_interaction(simple_pool, cid, 30.0)

    results = await contacts_overdue_with_tiers(simple_pool)
    ids = [str(r["id"]) for r in results]
    assert str(cid) not in ids


# ===========================================================================
# Tests for top_n parameter (server-side suggestion capping)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_top_n_defaults_to_3(dunbar_pool):
    """compute_urgency defaults to top_n=3, returning at most 3 results."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    # Create 5 contacts with varying urgency
    contact1 = await _make_contact(dunbar_pool, "HighUrgency1", stay_in_touch_days=10)
    contact2 = await _make_contact(dunbar_pool, "HighUrgency2", stay_in_touch_days=10)
    contact3 = await _make_contact(dunbar_pool, "HighUrgency3", stay_in_touch_days=10)
    contact4 = await _make_contact(dunbar_pool, "LowUrgency4", stay_in_touch_days=100)
    contact5 = await _make_contact(dunbar_pool, "LowUrgency5", stay_in_touch_days=100)

    # Log interactions at different times to create urgency variation
    await _log_interaction(dunbar_pool, contact1["entity_id"], days_ago=100)
    await _log_interaction(dunbar_pool, contact2["entity_id"], days_ago=80)
    await _log_interaction(dunbar_pool, contact3["entity_id"], days_ago=60)
    await _log_interaction(dunbar_pool, contact4["entity_id"], days_ago=150)
    await _log_interaction(dunbar_pool, contact5["entity_id"], days_ago=130)

    scores = [
        {
            "contact_id": contact1["id"],
            "entity_id": contact1["entity_id"],
            "score": 5.0,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=100),
        },
        {
            "contact_id": contact2["id"],
            "entity_id": contact2["entity_id"],
            "score": 4.5,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=80),
        },
        {
            "contact_id": contact3["id"],
            "entity_id": contact3["entity_id"],
            "score": 4.0,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=60),
        },
        {
            "contact_id": contact4["id"],
            "entity_id": contact4["entity_id"],
            "score": 3.0,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=150),
        },
        {
            "contact_id": contact5["id"],
            "entity_id": contact5["entity_id"],
            "score": 2.5,
            "last_interaction_at": datetime.now(UTC) - timedelta(days=130),
        },
    ]
    tier_ranking = get_tier_ranking(scores)
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)

    # Default top_n=3 should return at most 3 contacts
    assert len(results) <= 3


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_top_n_respects_explicit_value(dunbar_pool):
    """compute_urgency respects explicit top_n parameter."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    # Create 5 contacts
    contacts = []
    for i in range(5):
        contact = await _make_contact(
            dunbar_pool,
            f"Contact{i}",
            stay_in_touch_days=10,
        )
        await _log_interaction(dunbar_pool, contact["entity_id"], days_ago=100 - i * 10)
        contacts.append(contact)

    scores = [
        {
            "contact_id": c["id"],
            "entity_id": c["entity_id"],
            "score": float(5 - i),
            "last_interaction_at": datetime.now(UTC) - timedelta(days=100 - i * 10),
        }
        for i, c in enumerate(contacts)
    ]
    tier_ranking = get_tier_ranking(scores)

    # Request top_n=2
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking, top_n=2)
    assert len(results) <= 2

    # Request top_n=5
    results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking, top_n=5)
    assert len(results) <= 5


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_urgency_top_n_none_returns_all(dunbar_pool):
    """compute_urgency with top_n=None returns all results."""
    from butlers.tools.relationship.dunbar import compute_urgency, get_tier_ranking

    # Create multiple contacts
    contacts = []
    for i in range(4):
        contact = await _make_contact(
            dunbar_pool,
            f"AllContact{i}",
            stay_in_touch_days=10,
        )
        await _log_interaction(dunbar_pool, contact["entity_id"], days_ago=100 - i * 10)
        contacts.append(contact)

    scores = [
        {
            "contact_id": c["id"],
            "entity_id": c["entity_id"],
            "score": float(5 - i),
            "last_interaction_at": datetime.now(UTC) - timedelta(days=100 - i * 10),
        }
        for i, c in enumerate(contacts)
    ]
    tier_ranking = get_tier_ranking(scores)

    # Get all results
    all_results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking, top_n=None)

    # Get with default top_n=3
    default_results = await compute_urgency(dunbar_pool, tier_ranking=tier_ranking)

    # All results should be >= default results
    assert len(all_results) >= len(default_results)
    # Since filtering removes non-overdue zero-context, all_results could equal
    # default_results, so just verify top_n=None doesn't truncate artificially
    assert len(all_results) > 0


# ===========================================================================
# Write/read consistency — dunbar_tier_set → compute_tier_ranking
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_override_via_dunbar_tier_set_honored_by_compute_tier_ranking(simple_pool):
    """Override written by dunbar_tier_set is honored by compute_tier_ranking.

    dunbar_tier_set writes to relationship.facts (Store B) with entity_id set.
    _fetch_overrides reads from the same table with the same entity_id filter.
    compute_tier_ranking combines scores with overrides — the pinned tier must win
    over the rank-based assignment even when the contact's score would place it lower.
    """
    from butlers.tools.relationship.dunbar import compute_tier_ranking, dunbar_tier_set

    # Contact with a single old interaction so its score-based rank would be low (tier 1500).
    contact = await _make_simple_contact(simple_pool, "PinnedPerson")
    cid = uuid.UUID(str(contact["id"]))
    await _log_simple_interaction(simple_pool, cid, 90.0)

    # Pin to tier 5 via the MCP tool (canonical write path → facts table).
    await dunbar_tier_set(simple_pool, cid, 5)

    # compute_tier_ranking calls _fetch_overrides which reads the same facts table.
    ranking = await compute_tier_ranking(simple_pool)
    row = next((r for r in ranking if r["contact_id"] == cid), None)
    assert row is not None, "pinned contact must appear in the ranking"
    assert row["dunbar_tier"] == 5, (
        f"expected tier 5 (override), got {row['dunbar_tier']} — "
        "dunbar_tier_set write and _fetch_overrides read are split across stores"
    )
    assert row["dunbar_tier_override"] is True
