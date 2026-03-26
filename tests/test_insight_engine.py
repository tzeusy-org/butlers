"""Integration tests: end-to-end proactive insight engine.

Covers per bu-e38f:
1. End-to-end flow: insight-scan → propose_insight_candidate → delivery cycle → notify
2. Cross-butler deduplication (same dedup_key, highest priority wins)
3. Cooldown enforcement across cycles
4. Adaptive delivery with low engagement
5. Quiet hours skip
6. verbosity=off filtering
7. Digest formatting with multiple butlers
8. dedup_key format validation errors

These tests are unit-level (no Docker required) — they use in-process asyncpg
mocks or an in-memory SQLite-style fixture via inline table creation on a real
Postgres container fixture when available. For CI portability we use the
``provisioned_postgres_pool`` session fixture for DB-backed scenarios, but
mark Docker-requiring tests so they are skipped when Docker is unavailable.

Issue: bu-e38f
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

_docker_available = shutil.which("docker") is not None

# ---------------------------------------------------------------------------
# Marks: unit tests run without Docker; DB tests require Docker
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.asyncio(loop_scope="session")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future(days: int = 7) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _past(days: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


# ---------------------------------------------------------------------------
# Fixture: per-test database with insight tables
# ---------------------------------------------------------------------------


@pytest.fixture
async def insight_pool(provisioned_postgres_pool):
    """Provision a fresh database with insight tables for one test."""
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    async with provisioned_postgres_pool() as pool:
        await create_insight_tables(pool)
        yield pool


# ===========================================================================
# Category 1: InsightCandidate dataclass (unit, no Docker)
# ===========================================================================


class TestInsightCandidateModel:
    """Validates InsightCandidate dataclass construction and serialization."""

    def test_valid_candidate_construction(self):
        """InsightCandidate with valid args constructs without error."""
        from butlers.tools.switchboard.insight.models import InsightCandidate

        c = InsightCandidate(
            priority=75,
            category="birthday",
            dedup_key="birthday:entity-123:2026",
            message="Alice's birthday is in 3 days",
            expires_at=_future(),
        )
        assert c.priority == 75
        assert c.category == "birthday"
        assert c.dedup_key == "birthday:entity-123:2026"

    def test_priority_below_1_raises(self):
        """priority=0 raises ValueError."""
        from butlers.tools.switchboard.insight.models import InsightCandidate

        with pytest.raises(ValueError, match="priority must be between 1 and 100"):
            InsightCandidate(
                priority=0,
                category="birthday",
                dedup_key="birthday:entity-123:2026",
                message="test",
                expires_at=_future(),
            )

    def test_priority_above_100_raises(self):
        """priority=150 raises ValueError."""
        from butlers.tools.switchboard.insight.models import InsightCandidate

        with pytest.raises(ValueError, match="priority must be between 1 and 100"):
            InsightCandidate(
                priority=150,
                category="birthday",
                dedup_key="birthday:entity-123:2026",
                message="test",
                expires_at=_future(),
            )

    def test_empty_dedup_key_raises(self):
        """Empty dedup_key raises ValueError."""
        from butlers.tools.switchboard.insight.models import InsightCandidate

        with pytest.raises(ValueError, match="dedup_key"):
            InsightCandidate(
                priority=50,
                category="birthday",
                dedup_key="",
                message="test",
                expires_at=_future(),
            )

    def test_invalid_dedup_key_format_raises(self):
        """dedup_key without colons raises ValueError."""
        from butlers.tools.switchboard.insight.models import InsightCandidate

        with pytest.raises(ValueError, match="dedup_key must match format"):
            InsightCandidate(
                priority=50,
                category="birthday",
                dedup_key="nodots-here",
                message="test",
                expires_at=_future(),
            )

    def test_to_mcp_args_returns_expected_keys(self):
        """to_mcp_args() returns all required fields."""
        from butlers.tools.switchboard.insight.models import InsightCandidate

        c = InsightCandidate(
            priority=75,
            category="birthday",
            dedup_key="birthday:entity-123:2026",
            message="Alice's birthday is in 3 days",
            expires_at=_future(),
            cooldown_days=3,
            channel="telegram",
            metadata={"entity_id": "abc-123"},
        )
        args = c.to_mcp_args()
        assert args["priority"] == 75
        assert args["category"] == "birthday"
        assert args["dedup_key"] == "birthday:entity-123:2026"
        assert args["message"] == "Alice's birthday is in 3 days"
        assert "expires_at" in args
        assert args["cooldown_days"] == 3
        assert args["channel"] == "telegram"
        assert args["metadata"] == {"entity_id": "abc-123"}

    def test_to_mcp_args_omits_none_optional_fields(self):
        """to_mcp_args() omits optional fields that are None."""
        from butlers.tools.switchboard.insight.models import InsightCandidate

        c = InsightCandidate(
            priority=50,
            category="health",
            dedup_key="health:bp-log:2026-w13",
            message="No blood pressure logged this week",
            expires_at=_future(),
        )
        args = c.to_mcp_args()
        assert "cooldown_days" not in args
        assert "channel" not in args
        assert "metadata" not in args

    def test_four_segment_dedup_key_is_valid(self):
        """4-segment dedup_key (butler:category:entity:time) is accepted."""
        from butlers.tools.switchboard.insight.models import InsightCandidate

        c = InsightCandidate(
            priority=50,
            category="health",
            dedup_key="health:bp:user-1:2026-w13",
            message="Butler-specific insight",
            expires_at=_future(),
        )
        assert c.dedup_key == "health:bp:user-1:2026-w13"


# ===========================================================================
# Category 2: propose_insight_candidate validation (unit, no Docker)
# ===========================================================================


class TestProposeInsightCandidateValidation:
    """Validates the propose_insight_candidate tool's server-side validation."""

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_priority_out_of_range_returns_error(self, insight_pool):
        """priority=0 → error without inserting a row."""
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        result = await propose_insight_candidate(
            insight_pool,
            origin_butler="health",
            priority=0,
            category="health",
            dedup_key="health:bp:user-1:2026-w13",
            message="test",
            expires_at=_future(),
        )
        assert result["status"] == "error"
        assert "priority must be between 1 and 100" in result["reason"]

        row_count = await insight_pool.fetchval("SELECT COUNT(*) FROM insight_candidates")
        assert row_count == 0

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_empty_dedup_key_returns_error(self, insight_pool):
        """Empty dedup_key → error without inserting a row."""
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        result = await propose_insight_candidate(
            insight_pool,
            origin_butler="health",
            priority=70,
            category="health",
            dedup_key="",
            message="test",
            expires_at=_future(),
        )
        assert result["status"] == "error"
        assert "dedup_key is required" in result["reason"]

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_invalid_dedup_key_format_returns_error(self, insight_pool):
        """dedup_key with invalid format → error with format hint."""
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        result = await propose_insight_candidate(
            insight_pool,
            origin_butler="health",
            priority=70,
            category="health",
            dedup_key="invalid-no-colons",
            message="test",
            expires_at=_future(),
        )
        assert result["status"] == "error"
        assert "dedup_key must match format" in result["reason"]

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_expired_expires_at_returns_error(self, insight_pool):
        """expires_at in the past → error without inserting."""
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        result = await propose_insight_candidate(
            insight_pool,
            origin_butler="health",
            priority=70,
            category="health",
            dedup_key="health:bp:user-1:2026-w13",
            message="test",
            expires_at=_past(),
        )
        assert result["status"] == "error"
        assert "expires_at must be in the future" in result["reason"]

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_empty_message_returns_error(self, insight_pool):
        """Empty message → error without inserting."""
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        result = await propose_insight_candidate(
            insight_pool,
            origin_butler="health",
            priority=70,
            category="health",
            dedup_key="health:bp:user-1:2026-w13",
            message="",
            expires_at=_future(),
        )
        assert result["status"] == "error"
        assert "message must be non-empty" in result["reason"]

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_valid_candidate_is_accepted(self, insight_pool):
        """Valid candidate → accepted and inserted with status=pending."""
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        result = await propose_insight_candidate(
            insight_pool,
            origin_butler="relationship",
            priority=80,
            category="birthday",
            dedup_key="birthday:entity-123:2026",
            message="Alice's birthday is in 3 days",
            expires_at=_future(),
        )
        assert result["status"] == "accepted"
        assert "candidate queued" in result["reason"]

        row = await insight_pool.fetchrow(
            "SELECT * FROM insight_candidates WHERE dedup_key = $1",
            "birthday:entity-123:2026",
        )
        assert row is not None
        assert row["origin_butler"] == "relationship"
        assert row["priority"] == 80
        assert row["status"] == "pending"


# ===========================================================================
# Category 3: verbosity=off filtering (unit + DB)
# ===========================================================================


class TestVerbosityOff:
    """Validates verbosity=off behavior at propose time and delivery cycle."""

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_propose_filtered_when_verbosity_off(self, insight_pool):
        """propose_insight_candidate returns filtered when verbosity=off."""
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        # Set verbosity to off
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'off')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'off'
        """)

        result = await propose_insight_candidate(
            insight_pool,
            origin_butler="health",
            priority=70,
            category="health",
            dedup_key="health:bp:user-1:2026-w13",
            message="No BP logged",
            expires_at=_future(),
        )
        assert result["status"] == "filtered"
        assert result["reason"] == "verbosity is off"

        # No row inserted
        count = await insight_pool.fetchval("SELECT COUNT(*) FROM insight_candidates")
        assert count == 0

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_delivery_cycle_filters_all_when_verbosity_off(self, insight_pool):
        """delivery_cycle marks all pending candidates filtered when verbosity=off."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        # Manually insert a pending candidate (bypassing propose which would gate it)
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ('health', 70, 'health', 'health:bp:user-1:2026-w13', $1,
                    'No BP logged', 'pending')
        """,
            _future(),
        )

        # Set verbosity to off
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'off')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'off'
        """)

        result = await delivery_cycle(insight_pool)
        assert result["skipped"] is True

        status = await insight_pool.fetchval(
            "SELECT status FROM insight_candidates WHERE dedup_key = $1",
            "health:bp:user-1:2026-w13",
        )
        assert status == "filtered"

    def test_verbosity_budgets_mapping(self):
        """Verbosity preset budgets match spec."""
        from butlers.tools.switchboard.insight.broker import VERBOSITY_BUDGETS

        assert VERBOSITY_BUDGETS["off"] == 0
        assert VERBOSITY_BUDGETS["minimal"] == 1
        assert VERBOSITY_BUDGETS["normal"] == 3
        assert VERBOSITY_BUDGETS["verbose"] == 5


# ===========================================================================
# Category 4: End-to-end insight flow (requires Docker)
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestEndToEndInsightFlow:
    """End-to-end flow from propose_insight_candidate through delivery cycle to notify."""

    async def test_single_candidate_delivered_standalone(self, insight_pool):
        """Single candidate → standalone delivery, status=delivered, cooldown recorded."""
        from butlers.tools.switchboard.insight.broker import (
            delivery_cycle,
            propose_insight_candidate,
        )

        # Ensure minimal verbosity (default)
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        # Step 1: Propose a candidate (simulates butler insight-scan)
        result = await propose_insight_candidate(
            insight_pool,
            origin_butler="relationship",
            priority=80,
            category="birthday",
            dedup_key="birthday:entity-123:2026",
            message="Alice's birthday is in 3 days",
            expires_at=_future(),
        )
        assert result["status"] == "accepted"

        # Step 2: Run delivery cycle with a mock notify
        notify_mock = AsyncMock(return_value={"status": "sent"})
        cycle_result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        # Verify delivery
        assert not cycle_result["skipped"]
        assert len(cycle_result["delivered"]) == 1
        assert notify_mock.called

        # Message should be standalone (prefix + message)
        delivered_msg = cycle_result["delivery_message"]
        assert "[Relationship]" in delivered_msg
        assert "Alice's birthday is in 3 days" in delivered_msg

        # Candidate status → delivered
        row = await insight_pool.fetchrow(
            "SELECT status, delivered_at FROM insight_candidates WHERE dedup_key = $1",
            "birthday:entity-123:2026",
        )
        assert row["status"] == "delivered"
        assert row["delivered_at"] is not None

        # Cooldown recorded
        cooldown = await insight_pool.fetchrow(
            "SELECT * FROM insight_cooldowns WHERE dedup_key = $1",
            "birthday:entity-123:2026",
        )
        assert cooldown is not None
        assert cooldown["reason"] == "delivered"

        # Engagement row created
        engagement = await insight_pool.fetchrow("SELECT * FROM insight_engagement")
        assert engagement is not None
        assert engagement["engaged"] is False

    async def test_candidate_not_redelivered_after_delivery(self, insight_pool):
        """A delivered candidate is not re-delivered in a subsequent cycle."""
        from butlers.tools.switchboard.insight.broker import (
            delivery_cycle,
            propose_insight_candidate,
        )

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        await propose_insight_candidate(
            insight_pool,
            origin_butler="health",
            priority=70,
            category="health",
            dedup_key="health:bp:user-1:2026",
            message="No BP logged in 12 days",
            expires_at=_future(),
        )

        notify_mock = AsyncMock(return_value={"status": "sent"})

        # First cycle: should deliver
        r1 = await delivery_cycle(insight_pool, notify_fn=notify_mock)
        assert len(r1["delivered"]) == 1
        assert notify_mock.call_count == 1

        # Second cycle: same dedup_key under cooldown, no delivery
        r2 = await delivery_cycle(insight_pool, notify_fn=notify_mock)
        assert len(r2["delivered"]) == 0
        assert notify_mock.call_count == 1  # No additional call

    async def test_expired_candidate_not_delivered(self, insight_pool):
        """Candidates with expires_at in the past are marked expired, not delivered."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        # Insert an already-expired candidate manually
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ('health', 70, 'health', 'health:old:user-1:2025', $1,
                    'Old insight', 'pending')
        """,
            _past(2),
        )

        notify_mock = AsyncMock(return_value={"status": "sent"})
        result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        assert result["expired"] >= 1
        assert len(result["delivered"]) == 0
        assert not notify_mock.called

        status = await insight_pool.fetchval(
            "SELECT status FROM insight_candidates WHERE dedup_key = $1",
            "health:old:user-1:2025",
        )
        assert status == "expired"


# ===========================================================================
# Category 5: Cross-butler deduplication (requires Docker)
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestCrossButlerDeduplication:
    """Cross-butler deduplication: same dedup_key, highest priority wins."""

    async def test_highest_priority_wins_across_butlers(self, insight_pool):
        """Two butlers propose the same dedup_key; highest priority is delivered."""
        from butlers.tools.switchboard.insight.broker import (
            delivery_cycle,
            propose_insight_candidate,
        )

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        # Relationship butler: priority 80
        r1 = await propose_insight_candidate(
            insight_pool,
            origin_butler="relationship",
            priority=80,
            category="birthday",
            dedup_key="birthday:entity-123:2026",
            message="Alice's birthday is soon (relationship)",
            expires_at=_future(),
        )
        assert r1["status"] == "accepted"

        # Calendar butler: priority 60
        r2 = await propose_insight_candidate(
            insight_pool,
            origin_butler="calendar",
            priority=60,
            category="birthday",
            dedup_key="birthday:entity-123:2026",
            message="Alice's birthday is soon (calendar)",
            expires_at=_future(),
        )
        assert r2["status"] == "accepted"

        notify_mock = AsyncMock(return_value={"status": "sent"})
        cycle_result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        # Only 1 delivered
        assert len(cycle_result["delivered"]) == 1

        # The delivered candidate is the higher-priority one (relationship, priority=80)
        delivered_id = cycle_result["delivered"][0]
        row = await insight_pool.fetchrow(
            "SELECT origin_butler, priority, status FROM insight_candidates WHERE id = $1::uuid",
            delivered_id,
        )
        assert row["origin_butler"] == "relationship"
        assert row["priority"] == 80
        assert row["status"] == "delivered"

        # The lower-priority one should be filtered
        filtered_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates "
            "WHERE origin_butler = 'calendar' AND dedup_key = $1",
            "birthday:entity-123:2026",
        )
        assert filtered_row["status"] == "filtered"

    async def test_same_priority_ties_broken_by_created_at(self, insight_pool):
        """Tie on priority: earliest created_at wins."""
        from butlers.tools.switchboard.insight.broker import (
            deduplicate_candidates,
        )

        # Insert two candidates with same priority, different created_at
        now = datetime.now(UTC)
        id1 = str(uuid.uuid4())
        id2 = str(uuid.uuid4())

        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (id, origin_butler, priority, category, dedup_key, expires_at, message, status,
                 created_at)
            VALUES ($1::uuid, 'butler-a', 75, 'health', 'health:metric:user:2026', $2,
                    'First candidate', 'pending', $3)
        """,
            id1,
            _future(),
            now - timedelta(seconds=10),
        )

        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (id, origin_butler, priority, category, dedup_key, expires_at, message, status,
                 created_at)
            VALUES ($1::uuid, 'butler-b', 75, 'health', 'health:metric:user:2026', $2,
                    'Second candidate', 'pending', $3)
        """,
            id2,
            _future(),
            now,
        )

        winning_ids = await deduplicate_candidates(insight_pool, [id1, id2])
        assert len(winning_ids) == 1
        # The earlier candidate (id1) should win
        assert id1 in winning_ids

    async def test_within_butler_dedup_filters_lower_priority(self, insight_pool):
        """Same butler, same dedup_key: lower priority candidate is filtered."""
        from butlers.tools.switchboard.insight.broker import (
            deduplicate_candidates,
        )

        id_high = str(uuid.uuid4())
        id_low = str(uuid.uuid4())

        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (id, origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ($1::uuid, 'health', 85, 'health', 'health:bp:user:2026', $2,
                    'High priority insight', 'pending')
        """,
            id_high,
            _future(),
        )

        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (id, origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ($1::uuid, 'health', 55, 'health', 'health:bp:user:2026', $2,
                    'Low priority insight', 'pending')
        """,
            id_low,
            _future(),
        )

        winning_ids = await deduplicate_candidates(insight_pool, [id_high, id_low])
        assert id_high in winning_ids
        assert id_low not in winning_ids

        # Loser is filtered
        status = await insight_pool.fetchval(
            "SELECT status FROM insight_candidates WHERE id = $1::uuid", id_low
        )
        assert status == "filtered"


# ===========================================================================
# Category 6: Cooldown enforcement (requires Docker)
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestCooldownEnforcement:
    """Cooldown enforcement: insights are not re-delivered within cooldown period."""

    async def test_candidate_filtered_by_active_cooldown(self, insight_pool):
        """Candidate with same dedup_key as active cooldown is filtered."""
        from butlers.tools.switchboard.insight.broker import filter_by_cooldown

        # Insert active cooldown
        await insight_pool.execute(
            """
            INSERT INTO insight_cooldowns (dedup_key, cooldown_until, reason)
            VALUES ($1, $2, 'delivered')
        """,
            "birthday:entity-123:2026",
            _future(7),
        )

        # Insert candidate
        cid = str(uuid.uuid4())
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (id, origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ($1::uuid, 'relationship', 80, 'birthday',
                    'birthday:entity-123:2026', $2, 'Birthday insight', 'pending')
        """,
            cid,
            _future(),
        )

        eligible = await filter_by_cooldown(insight_pool, [cid])
        assert cid not in eligible

        # Status is filtered
        status = await insight_pool.fetchval(
            "SELECT status FROM insight_candidates WHERE id = $1::uuid", cid
        )
        assert status == "filtered"

    async def test_candidate_eligible_after_cooldown_expires(self, insight_pool):
        """Candidate is eligible after cooldown_until is in the past."""
        from butlers.tools.switchboard.insight.broker import filter_by_cooldown

        # Insert expired cooldown
        await insight_pool.execute(
            """
            INSERT INTO insight_cooldowns (dedup_key, cooldown_until, reason)
            VALUES ($1, $2, 'delivered')
        """,
            "birthday:entity-123:2026",
            _past(1),
        )

        cid = str(uuid.uuid4())
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (id, origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ($1::uuid, 'relationship', 80, 'birthday',
                    'birthday:entity-123:2026', $2, 'Birthday insight again', 'pending')
        """,
            cid,
            _future(),
        )

        eligible = await filter_by_cooldown(insight_pool, [cid])
        assert cid in eligible

    async def test_default_cooldown_by_priority_range(self):
        """Default cooldowns match spec for each priority range."""
        from butlers.tools.switchboard.insight.broker import _get_default_cooldown

        assert _get_default_cooldown(95) == 1  # 90-100
        assert _get_default_cooldown(80) == 7  # 70-89
        assert _get_default_cooldown(60) == 14  # 50-69
        assert _get_default_cooldown(40) == 30  # 30-49
        assert _get_default_cooldown(15) == 30  # 1-29

    async def test_custom_cooldown_override(self, insight_pool):
        """Custom cooldown_days overrides the default."""
        from butlers.tools.switchboard.insight.broker import record_cooldowns

        candidate = {
            "id": str(uuid.uuid4()),
            "origin_butler": "relationship",
            "priority": 80,
            "category": "birthday",
            "dedup_key": "birthday:custom:user:2026",
            "message": "Custom cooldown test",
            "cooldown_days": 3,
        }
        now = datetime.now(UTC)
        await record_cooldowns(insight_pool, [candidate], now=now)

        row = await insight_pool.fetchrow(
            "SELECT cooldown_until FROM insight_cooldowns WHERE dedup_key = $1",
            "birthday:custom:user:2026",
        )
        assert row is not None
        expected = now + timedelta(days=3)
        diff = abs((row["cooldown_until"].replace(tzinfo=UTC) - expected).total_seconds())
        assert diff < 2  # Within 2 seconds


# ===========================================================================
# Category 7: Adaptive delivery with low engagement (requires Docker)
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestAdaptiveDelivery:
    """Adaptive delivery: budget reduces when user engagement is low."""

    async def test_full_budget_when_engagement_above_50_percent(self, insight_pool):
        """engagement_rate >= 0.5 → full configured budget."""
        from butlers.tools.switchboard.insight.broker import compute_effective_budget

        settings = {"verbosity": "normal", "custom_budget": None}
        now = datetime.now(UTC)
        # Insert 10 deliveries, 6 engaged (60%)
        for i in range(6):
            await insight_pool.execute(
                """
                INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
                VALUES ($1::uuid, $2, TRUE)
            """,
                str(uuid.uuid4()),
                now - timedelta(days=i),
            )
        for i in range(4):
            await insight_pool.execute(
                """
                INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
                VALUES ($1::uuid, $2, FALSE)
            """,
                str(uuid.uuid4()),
                now - timedelta(days=i),
            )

        budget = await compute_effective_budget(insight_pool, settings, now=now)
        # normal budget=3, engagement=60% → no reduction
        assert budget == 3

    async def test_budget_reduced_one_when_moderate_disengagement(self, insight_pool):
        """0.25 <= engagement_rate < 0.5 → max(1, configured_budget - 1)."""
        from butlers.tools.switchboard.insight.broker import compute_effective_budget

        settings = {"verbosity": "normal", "custom_budget": None}
        now = datetime.now(UTC)
        # 3 engaged, 10 total → 30% engagement
        for i in range(3):
            await insight_pool.execute(
                """
                INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
                VALUES ($1::uuid, $2, TRUE)
            """,
                str(uuid.uuid4()),
                now - timedelta(days=i),
            )
        for i in range(7):
            await insight_pool.execute(
                """
                INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
                VALUES ($1::uuid, $2, FALSE)
            """,
                str(uuid.uuid4()),
                now - timedelta(days=i),
            )

        budget = await compute_effective_budget(insight_pool, settings, now=now)
        # normal budget=3, engagement=30% → max(1, 3-1) = 2
        assert budget == 2

    async def test_budget_becomes_1_when_severe_disengagement(self, insight_pool):
        """engagement_rate < 0.25 → effective budget = 1."""
        from butlers.tools.switchboard.insight.broker import compute_effective_budget

        settings = {"verbosity": "verbose", "custom_budget": None}
        now = datetime.now(UTC)
        # 1 engaged, 10 total → 10%
        await insight_pool.execute(
            """
            INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
            VALUES ($1::uuid, $2, TRUE)
        """,
            str(uuid.uuid4()),
            now - timedelta(days=1),
        )
        for i in range(9):
            await insight_pool.execute(
                """
                INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
                VALUES ($1::uuid, $2, FALSE)
            """,
                str(uuid.uuid4()),
                now - timedelta(days=i),
            )

        budget = await compute_effective_budget(insight_pool, settings, now=now)
        assert budget == 1

    async def test_no_penalty_when_no_engagement_history(self, insight_pool):
        """No deliveries in 14-day window → engagement_rate = 1.0 (no reduction)."""
        from butlers.tools.switchboard.insight.broker import compute_effective_budget

        settings = {"verbosity": "verbose", "custom_budget": None}
        budget = await compute_effective_budget(insight_pool, settings)
        assert budget == 5  # verbose budget with no history

    async def test_no_automatic_increase_after_improvement(self, insight_pool):
        """Improvement in engagement does NOT automatically restore budget.

        Once reduced by adaptive logic, the user MUST explicitly change their
        verbosity setting. We verify the engine uses the configured budget
        as ceiling — it never exceeds it.
        """
        from butlers.tools.switchboard.insight.broker import compute_effective_budget

        # Start with minimal budget=1
        settings = {"verbosity": "minimal", "custom_budget": None}
        now = datetime.now(UTC)
        # Perfect engagement
        for i in range(5):
            await insight_pool.execute(
                """
                INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
                VALUES ($1::uuid, $2, TRUE)
            """,
                str(uuid.uuid4()),
                now - timedelta(days=i),
            )

        budget = await compute_effective_budget(insight_pool, settings, now=now)
        # Engagement rate = 100%, configured = 1 → cannot exceed configured
        assert budget == 1


# ===========================================================================
# Category 8: Quiet hours suppression (unit, no Docker)
# ===========================================================================


class TestQuietHours:
    """Quiet hours: delivery is skipped during configured quiet hours."""

    def test_is_quiet_during_quiet_hours(self):
        """Returns True when current time is within quiet hours."""
        from butlers.tools.switchboard.insight.broker import _is_quiet_hours

        # Quiet from 22:00 to 08:00 (wraps midnight)
        settings = {
            "quiet_start": 22,
            "quiet_end": 8,
            "quiet_timezone": None,
        }
        # 23:30 UTC — should be quiet
        now = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
        assert _is_quiet_hours(settings, now=now) is True

    def test_is_not_quiet_outside_quiet_hours(self):
        """Returns False when current time is outside quiet hours."""
        from butlers.tools.switchboard.insight.broker import _is_quiet_hours

        settings = {
            "quiet_start": 22,
            "quiet_end": 8,
            "quiet_timezone": None,
        }
        # 12:00 UTC — active hours
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        assert _is_quiet_hours(settings, now=now) is False

    def test_same_day_quiet_range(self):
        """Quiet hours that don't wrap midnight (e.g. 9-17)."""
        from butlers.tools.switchboard.insight.broker import _is_quiet_hours

        settings = {
            "quiet_start": 9,
            "quiet_end": 17,
            "quiet_timezone": None,
        }
        assert _is_quiet_hours(settings, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)) is True
        assert _is_quiet_hours(settings, now=datetime(2026, 1, 1, 8, 0, tzinfo=UTC)) is False
        assert _is_quiet_hours(settings, now=datetime(2026, 1, 1, 18, 0, tzinfo=UTC)) is False

    def test_no_quiet_hours_configured(self):
        """Returns False when no quiet hours configured."""
        from butlers.tools.switchboard.insight.broker import _is_quiet_hours

        settings = {"quiet_start": None, "quiet_end": None, "quiet_timezone": None}
        assert _is_quiet_hours(settings) is False

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_delivery_cycle_skips_during_quiet_hours(self, insight_pool):
        """delivery_cycle returns skipped=True when running during quiet hours."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        # Configure quiet hours 00:00-23:59 (always quiet)
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity, quiet_start, quiet_end)
            VALUES (1, 'normal', 0, 23)
            ON CONFLICT (id) DO UPDATE SET verbosity='normal', quiet_start=0, quiet_end=23
        """)

        # Insert a pending candidate
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ('health', 70, 'health', 'health:bp:user:2026', $1,
                    'No BP logged', 'pending')
        """,
            _future(),
        )

        notify_mock = AsyncMock(return_value={"status": "sent"})
        result = await delivery_cycle(
            insight_pool,
            notify_fn=notify_mock,
            now=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        )

        assert result["skipped"] is True
        assert len(result["delivered"]) == 0
        assert not notify_mock.called


# ===========================================================================
# Category 9: Digest formatting (unit, no Docker)
# ===========================================================================


class TestDigestFormatting:
    """Digest formatting: multiple butlers contribute to a single digest message."""

    def test_digest_header_with_count(self):
        """Digest message begins with 'Daily Insights (N):'."""
        from butlers.tools.switchboard.insight.broker import _format_digest

        candidates = [
            {"origin_butler": "relationship", "message": "Alice's birthday"},
            {"origin_butler": "health", "message": "Log blood pressure"},
            {"origin_butler": "finance", "message": "Unusual spending detected"},
        ]
        msg = _format_digest(candidates)
        assert msg.startswith("Daily Insights (3):")

    def test_digest_includes_butler_labels(self):
        """Each digest item includes the origin butler name in brackets."""
        from butlers.tools.switchboard.insight.broker import _format_digest

        candidates = [
            {"origin_butler": "relationship", "message": "Alice's birthday"},
            {"origin_butler": "health", "message": "Log BP"},
        ]
        msg = _format_digest(candidates)
        assert "[Relationship]" in msg
        assert "[Health]" in msg

    def test_digest_numbering(self):
        """Digest items are numbered 1, 2, 3, ..."""
        from butlers.tools.switchboard.insight.broker import _format_digest

        candidates = [
            {"origin_butler": "finance", "message": "Unusual spending"},
            {"origin_butler": "travel", "message": "Document expiring"},
        ]
        msg = _format_digest(candidates)
        assert "1." in msg
        assert "2." in msg

    def test_standalone_message_format(self):
        """Standalone delivery prefixes butler name and includes message."""
        from butlers.tools.switchboard.insight.broker import _format_standalone

        candidate = {
            "origin_butler": "health",
            "message": "You haven't logged blood pressure in 12 days",
        }
        msg = _format_standalone(candidate)
        assert "[Health]" in msg
        assert "You haven't logged blood pressure in 12 days" in msg

    def test_standalone_no_digest_framing(self):
        """Standalone message does NOT include 'Daily Insights' header."""
        from butlers.tools.switchboard.insight.broker import _format_standalone

        candidate = {
            "origin_butler": "health",
            "message": "Some message",
        }
        msg = _format_standalone(candidate)
        assert "Daily Insights" not in msg

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_multiple_candidates_delivered_as_digest(self, insight_pool):
        """Budget > 1 causes multiple candidates to be delivered in digest format."""
        from butlers.tools.switchboard.insight.broker import (
            delivery_cycle,
            propose_insight_candidate,
        )

        # Set verbosity to normal (budget=3)
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'normal'
        """)

        # Propose 3 candidates from different butlers with different dedup_keys
        for butler, dedup, msg, priority in [
            ("relationship", "birthday:alice:2026", "Alice's birthday", 85),
            ("health", "health:bp:user:2026", "Log BP", 70),
            ("finance", "finance:spend:user:2026", "Unusual spending", 60),
        ]:
            r = await propose_insight_candidate(
                insight_pool,
                origin_butler=butler,
                priority=priority,
                category=butler,
                dedup_key=dedup,
                message=msg,
                expires_at=_future(),
            )
            assert r["status"] == "accepted"

        notify_mock = AsyncMock(return_value={"status": "sent"})
        result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        assert len(result["delivered"]) == 3
        digest_msg = result["delivery_message"]
        assert "Daily Insights (3):" in digest_msg
        assert "[Relationship]" in digest_msg
        assert "[Health]" in digest_msg
        assert "[Finance]" in digest_msg

        # All 3 candidates share the same delivered_at timestamp
        rows = await insight_pool.fetch(
            "SELECT delivered_at FROM insight_candidates WHERE status = 'delivered'"
        )
        assert len(rows) == 3
        timestamps = [r["delivered_at"] for r in rows]
        # They should all be equal (set within the same delivery_cycle call)
        assert all(t == timestamps[0] for t in timestamps)


# ===========================================================================
# Category 10: Budget enforcement (requires Docker)
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestBudgetEnforcement:
    """Global delivery budget limits the number of insights per cycle."""

    async def test_budget_limits_deliveries(self, insight_pool):
        """At most B candidates are delivered per cycle (budget=1 with 3 candidates)."""
        from butlers.tools.switchboard.insight.broker import (
            delivery_cycle,
            propose_insight_candidate,
        )

        # minimal verbosity: budget=1
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        # Propose 3 candidates with different dedup_keys
        for i in range(3):
            await propose_insight_candidate(
                insight_pool,
                origin_butler="health",
                priority=70 + i,
                category="health",
                dedup_key=f"health:metric-{i}:user:2026",
                message=f"Insight {i}",
                expires_at=_future(),
            )

        notify_mock = AsyncMock(return_value={"status": "sent"})
        result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        # Only 1 delivered (minimal budget)
        assert len(result["delivered"]) == 1
        assert result["effective_budget"] == 1

        # Remaining 2 should still be pending (not filtered or expired)
        pending = await insight_pool.fetchval(
            "SELECT COUNT(*) FROM insight_candidates WHERE status = 'pending'"
        )
        assert pending == 2

    async def test_highest_priority_candidate_wins_under_budget(self, insight_pool):
        """When budget=1, the highest-priority candidate is selected."""
        from butlers.tools.switchboard.insight.broker import (
            delivery_cycle,
            propose_insight_candidate,
        )

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        # Low priority
        await propose_insight_candidate(
            insight_pool,
            origin_butler="health",
            priority=40,
            category="health",
            dedup_key="health:low:user:2026",
            message="Low priority insight",
            expires_at=_future(),
        )
        # High priority
        await propose_insight_candidate(
            insight_pool,
            origin_butler="relationship",
            priority=95,
            category="birthday",
            dedup_key="birthday:alice:2026",
            message="Critical birthday insight",
            expires_at=_future(),
        )

        notify_mock = AsyncMock(return_value={"status": "sent"})
        result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        assert len(result["delivered"]) == 1
        delivered_id = result["delivered"][0]
        row = await insight_pool.fetchrow(
            "SELECT priority FROM insight_candidates WHERE id = $1::uuid",
            delivered_id,
        )
        assert row["priority"] == 95


# ===========================================================================
# Category 11: Cleanup (requires Docker)
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestCleanup:
    """Periodic cleanup removes stale rows from insight tables."""

    async def test_old_non_pending_candidates_deleted(self, insight_pool):
        """Delivered/expired/filtered candidates older than 30 days are deleted."""
        from butlers.tools.switchboard.insight.broker import cleanup_old_rows

        old_time = datetime.now(UTC) - timedelta(days=35)
        recent_time = datetime.now(UTC) - timedelta(days=5)

        # Old delivered candidate
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message,
                 status, created_at)
            VALUES ('health', 70, 'health', 'health:old:user:2020', $1, 'Old', 'delivered', $2)
        """,
            _past(35),
            old_time,
        )

        # Recent delivered candidate
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message,
                 status, created_at)
            VALUES ('health', 70, 'health', 'health:recent:user:2026', $1, 'Recent',
                    'delivered', $2)
        """,
            _future(),
            recent_time,
        )

        # Pending candidate (should NOT be deleted)
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ('health', 70, 'health', 'health:pending:user:2026', $1, 'Pending', 'pending')
        """,
            _future(),
        )

        await cleanup_old_rows(insight_pool)

        remaining = await insight_pool.fetch("SELECT dedup_key, status FROM insight_candidates")
        dedup_keys = {r["dedup_key"] for r in remaining}

        assert "health:old:user:2020" not in dedup_keys
        assert "health:recent:user:2026" in dedup_keys
        assert "health:pending:user:2026" in dedup_keys

    async def test_old_cooldowns_deleted(self, insight_pool):
        """Cooldown rows older than 30 days past their expiry are deleted."""
        from butlers.tools.switchboard.insight.broker import cleanup_old_rows

        old_cooldown_until = datetime.now(UTC) - timedelta(days=35)
        recent_cooldown_until = datetime.now(UTC) + timedelta(days=5)

        await insight_pool.execute(
            """
            INSERT INTO insight_cooldowns (dedup_key, cooldown_until, reason)
            VALUES ($1, $2, 'delivered')
        """,
            "old-key:entity:2020",
            old_cooldown_until,
        )

        await insight_pool.execute(
            """
            INSERT INTO insight_cooldowns (dedup_key, cooldown_until, reason)
            VALUES ($1, $2, 'delivered')
        """,
            "active-key:entity:2026",
            recent_cooldown_until,
        )

        await cleanup_old_rows(insight_pool)

        rows = await insight_pool.fetch("SELECT dedup_key FROM insight_cooldowns")
        keys = {r["dedup_key"] for r in rows}
        assert "old-key:entity:2020" not in keys
        assert "active-key:entity:2026" in keys

    async def test_old_engagement_rows_deleted(self, insight_pool):
        """Engagement rows older than 30 days are deleted."""
        from butlers.tools.switchboard.insight.broker import cleanup_old_rows

        old_delivered_at = datetime.now(UTC) - timedelta(days=35)
        recent_delivered_at = datetime.now(UTC) - timedelta(days=5)
        old_id = str(uuid.uuid4())
        recent_id = str(uuid.uuid4())

        await insight_pool.execute(
            """
            INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
            VALUES ($1::uuid, $2, FALSE)
        """,
            old_id,
            old_delivered_at,
        )

        await insight_pool.execute(
            """
            INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
            VALUES ($1::uuid, $2, FALSE)
        """,
            recent_id,
            recent_delivered_at,
        )

        await cleanup_old_rows(insight_pool)

        rows = await insight_pool.fetch("SELECT insight_id FROM insight_engagement")
        ids = {str(r["insight_id"]) for r in rows}
        assert old_id not in ids
        assert recent_id in ids
