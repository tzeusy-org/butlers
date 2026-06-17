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
    """Validates InsightCandidate dataclass construction, validation, and serialization."""

    def test_valid_construction_and_mcp_serialization(self):
        """InsightCandidate constructs and to_mcp_args() returns correct fields."""
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
        assert c.priority == 75
        assert c.dedup_key == "birthday:entity-123:2026"

        args = c.to_mcp_args()
        assert args["priority"] == 75
        assert args["cooldown_days"] == 3
        assert args["channel"] == "telegram"
        assert args["metadata"] == {"entity_id": "abc-123"}

        # Optional fields omitted when None
        c2 = InsightCandidate(
            priority=50,
            category="health",
            dedup_key="health:bp-log:2026-w13",
            message="No BP",
            expires_at=_future(),
        )
        args2 = c2.to_mcp_args()
        assert "cooldown_days" not in args2
        assert "channel" not in args2

    @pytest.mark.parametrize(
        "kwargs,match",
        [
            ({"priority": 0}, "priority must be between 1 and 100"),
            ({"priority": 150}, "priority must be between 1 and 100"),
            ({"dedup_key": ""}, "dedup_key"),
            ({"dedup_key": "nodots-here"}, "dedup_key must match format"),
        ],
        ids=["priority-low", "priority-high", "empty-dedup", "invalid-dedup-format"],
    )
    def test_validation_errors(self, kwargs, match):
        from butlers.tools.switchboard.insight.models import InsightCandidate

        base = {
            "priority": 50,
            "category": "birthday",
            "dedup_key": "birthday:entity-123:2026",
            "message": "test",
            "expires_at": _future(),
        }
        base.update(kwargs)
        with pytest.raises(ValueError, match=match):
            InsightCandidate(**base)

    def test_four_segment_dedup_key_is_valid(self):
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
class TestVerbosityOff:
    """Validates verbosity=off behavior at propose time and delivery cycle."""

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

    @pytest.mark.parametrize(
        "start,end,hour,expected",
        [
            (22, 8, 23, True),  # midnight wrap, inside
            (22, 8, 12, False),  # midnight wrap, outside
            (9, 17, 12, True),  # same-day, inside
            (9, 17, 8, False),  # same-day, before
            (9, 17, 18, False),  # same-day, after
        ],
        ids=["wrap-inside", "wrap-outside", "same-inside", "same-before", "same-after"],
    )
    def test_is_quiet_hours_ranges(self, start, end, hour, expected):
        from butlers.tools.switchboard.insight.broker import _is_quiet_hours

        settings = {"quiet_start": start, "quiet_end": end, "quiet_timezone": None}
        now = datetime(2026, 1, 15, hour, 0, tzinfo=UTC)
        assert _is_quiet_hours(settings, now=now) is expected

    def test_no_quiet_hours_configured(self):
        from butlers.tools.switchboard.insight.broker import _is_quiet_hours

        settings = {"quiet_start": None, "quiet_end": None, "quiet_timezone": None}
        assert _is_quiet_hours(settings) is False

    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    @pytest.mark.integration
    async def test_delivery_cycle_skips_during_quiet_hours(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity, quiet_start, quiet_end)
            VALUES (1, 'normal', 0, 23)
            ON CONFLICT (id) DO UPDATE SET verbosity='normal', quiet_start=0, quiet_end=23
        """)
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
        assert not notify_mock.called


# ===========================================================================
# Category 9: Digest formatting (unit, no Docker)
# ===========================================================================


class TestDigestFormatting:
    """Digest formatting: multiple butlers contribute to a single digest message."""

    def test_digest_header_labels_and_numbering(self):
        """Digest starts with count header, includes butler labels, and is numbered."""
        from butlers.tools.switchboard.insight.broker import _format_digest

        candidates = [
            {"origin_butler": "relationship", "message": "Alice's birthday"},
            {"origin_butler": "health", "message": "Log blood pressure"},
            {"origin_butler": "finance", "message": "Unusual spending detected"},
        ]
        msg = _format_digest(candidates)
        assert msg.startswith("Daily Insights (3):")
        assert "[Relationship]" in msg
        assert "[Health]" in msg
        assert "1." in msg and "2." in msg

    def test_standalone_format(self):
        """Standalone delivery prefixes butler name, excludes digest framing."""
        from butlers.tools.switchboard.insight.broker import _format_standalone

        candidate = {"origin_butler": "health", "message": "No BP in 12 days"}
        msg = _format_standalone(candidate)
        assert "[Health]" in msg
        assert "No BP in 12 days" in msg
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


# ===========================================================================
# Category: Delivery attempt tracking and repeated-failure filtering [bu-a3wr]
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestDeliveryAttemptTracking:
    """delivery_cycle increments delivery_attempt_count on failure and filters after 3."""

    async def test_failed_delivery_increments_attempt_count(self, insight_pool):
        """When notify_fn returns error, delivery_attempt_count is incremented."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        # Ensure budget is available
        await insight_pool.execute("UPDATE insight_settings SET verbosity = 'normal' WHERE id = 1")

        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ('health', 80, 'health', 'health:bp:user:2026', $1, 'Blood pressure alert',
                    'pending')
            """,
            _future(),
        )

        async def failing_notify(message, metadata):
            return {"status": "error", "error": "channel unavailable"}

        result = await delivery_cycle(insight_pool, notify_fn=failing_notify)

        # Candidate should NOT be delivered
        assert result["delivered"] == []

        row = await insight_pool.fetchrow(
            "SELECT delivery_attempt_count, status FROM insight_candidates "
            "WHERE dedup_key = 'health:bp:user:2026'"
        )
        assert row["delivery_attempt_count"] == 1
        assert row["status"] == "pending"

    async def test_candidate_filtered_after_three_failures(self, insight_pool):
        """After 3 failed delivery attempts, candidate is marked filtered."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("UPDATE insight_settings SET verbosity = 'normal' WHERE id = 1")

        # Pre-seed with 2 prior failures so next failure triggers filter
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message,
                 status, delivery_attempt_count)
            VALUES ('health', 80, 'health', 'health:bp:user:2026-retry', $1,
                    'Blood pressure retry', 'pending', 2)
            """,
            _future(),
        )

        async def failing_notify(message, metadata):
            return {"status": "error", "error": "channel unavailable"}

        await delivery_cycle(insight_pool, notify_fn=failing_notify)

        row = await insight_pool.fetchrow(
            "SELECT delivery_attempt_count, status FROM insight_candidates "
            "WHERE dedup_key = 'health:bp:user:2026-retry'"
        )
        assert row["delivery_attempt_count"] == 3
        assert row["status"] == "filtered"

    async def test_successful_delivery_does_not_increment_count(self, insight_pool):
        """Successful delivery does not touch delivery_attempt_count."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("UPDATE insight_settings SET verbosity = 'minimal' WHERE id = 1")

        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ('health', 80, 'health', 'health:bp:user:2026-ok', $1,
                    'Blood pressure ok', 'pending')
            """,
            _future(),
        )

        notify_mock = AsyncMock(return_value={"status": "ok"})
        await delivery_cycle(insight_pool, notify_fn=notify_mock)

        row = await insight_pool.fetchrow(
            "SELECT delivery_attempt_count, status FROM insight_candidates "
            "WHERE dedup_key = 'health:bp:user:2026-ok'"
        )
        assert row["delivery_attempt_count"] == 0
        assert row["status"] == "delivered"

    async def test_notify_fn_none_skips_delivery_without_marking_candidates(self, insight_pool):
        """When notify_fn=None, delivery_cycle skips delivery; candidates stay pending.

        Previously, a None notify_fn caused deliver_success to remain True
        (since no notify was called to set it False), silently marking
        candidates as delivered without sending anything.  The fix returns
        early with skipped=True so no candidates are consumed.
        """
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("UPDATE insight_settings SET verbosity = 'normal' WHERE id = 1")

        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message, status)
            VALUES ('health', 80, 'health', 'health:bp:none-fn:2026', $1,
                    'Blood pressure check', 'pending')
            """,
            _future(),
        )

        result = await delivery_cycle(insight_pool, notify_fn=None)

        assert result["skipped"] is True
        assert result["delivered"] == []

        row = await insight_pool.fetchrow(
            "SELECT status, delivery_attempt_count FROM insight_candidates "
            "WHERE dedup_key = 'health:bp:none-fn:2026'"
        )
        # Candidate must remain pending — not silently consumed
        assert row["status"] == "pending"
        assert row["delivery_attempt_count"] == 0

    async def test_delivery_attempt_count_reset_on_success_after_prior_failures(self, insight_pool):
        """Successful delivery resets delivery_attempt_count to 0.

        Previously, a candidate that failed twice then succeeded on the
        third attempt would retain count=2.  A subsequent failure would
        push it to count=3 and trigger filtering even though only 1
        consecutive failure had occurred.  The fix resets the counter on
        every successful delivery.
        """
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("UPDATE insight_settings SET verbosity = 'minimal' WHERE id = 1")

        # Pre-seed with 2 prior failures (mimics fail-fail history)
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message,
                 status, delivery_attempt_count)
            VALUES ('health', 80, 'health', 'health:bp:reset-test:2026', $1,
                    'Blood pressure reset', 'pending', 2)
            """,
            _future(),
        )

        notify_mock = AsyncMock(return_value={"status": "ok"})
        await delivery_cycle(insight_pool, notify_fn=notify_mock)

        row = await insight_pool.fetchrow(
            "SELECT status, delivery_attempt_count FROM insight_candidates "
            "WHERE dedup_key = 'health:bp:reset-test:2026'"
        )
        assert row["status"] == "delivered"
        # Counter must be reset to 0, not left at 2
        assert row["delivery_attempt_count"] == 0


# ===========================================================================
# Category: Auto-off on total disengagement [bu-a3wr]
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestAutoOffTotalDisengagement:
    """check_total_disengagement_auto_off triggers when engagement==0 for 14 days."""

    async def _insert_daily_engagement(
        self,
        pool,
        *,
        num_days: int,
        engaged: bool = False,
        insights_per_day: int = 1,
        reference_now: datetime | None = None,
    ) -> None:
        """Insert engagement rows for num_days complete past days.

        Each row is anchored to midnight-based day boundaries so the data
        falls reliably within the check_total_disengagement_auto_off window,
        which uses midnight-anchored boundaries with an exclusive upper bound.

        Days are placed at day -1, -2, ..., -num_days (yesterday and earlier),
        never on today (which is excluded by the window's < today_midnight bound).
        """
        if reference_now is None:
            reference_now = datetime.now(UTC)
        # Anchor to today's midnight so offsets map to complete calendar days
        today_midnight = reference_now.replace(hour=0, minute=0, second=0, microsecond=0)
        for day_offset in range(num_days):
            # Place data at 01:00 on day -(num_days - day_offset), working
            # from the earliest day forward. All days are in the past (>= day -num_days
            # and <= day -1), safely below the exclusive window_end (today_midnight).
            day_ts = today_midnight - timedelta(days=num_days - day_offset) + timedelta(hours=1)
            for _ in range(insights_per_day):
                insight_id = str(uuid.uuid4())
                await pool.execute(
                    """
                    INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
                    VALUES ($1::uuid, $2, $3)
                    """,
                    insight_id,
                    day_ts,
                    engaged,
                )

    async def test_auto_off_triggered_when_zero_engagement_14_days(self, insight_pool):
        """auto-off fires after 14 days of zero engagement with daily deliveries."""
        from butlers.tools.switchboard.insight.broker import (
            check_total_disengagement_auto_off,
            get_insight_settings,
        )

        now = datetime.now(UTC)
        await self._insert_daily_engagement(
            insight_pool, num_days=14, engaged=False, reference_now=now
        )

        notify_mock = AsyncMock(return_value={"status": "ok"})
        triggered = await check_total_disengagement_auto_off(
            insight_pool, now=now, notify_fn=notify_mock
        )

        assert triggered is True
        settings = await get_insight_settings(insight_pool)
        assert settings["verbosity"] == "off"
        notify_mock.assert_called_once()
        call_args = notify_mock.call_args[0]
        assert "paused proactive insights" in call_args[0]

    async def test_auto_off_not_triggered_insufficient_data(self, insight_pool):
        """auto-off doesn't fire with partial engagement, <14 days, or no history."""
        from butlers.tools.switchboard.insight.broker import (
            check_total_disengagement_auto_off,
            get_insight_settings,
        )

        # No history at all
        assert await check_total_disengagement_auto_off(insight_pool) is False

        # Only 13 days of zero engagement (fewer than 14)
        now = datetime.now(UTC)
        await self._insert_daily_engagement(
            insight_pool, num_days=13, engaged=False, reference_now=now
        )
        assert await check_total_disengagement_auto_off(insight_pool, now=now) is False
        settings = await get_insight_settings(insight_pool)
        assert settings["verbosity"] != "off"

    async def test_auto_off_not_triggered_with_partial_engagement(self, insight_pool):
        """auto-off does not fire when at least one day had engagement."""
        from butlers.tools.switchboard.insight.broker import (
            check_total_disengagement_auto_off,
        )

        now = datetime.now(UTC)
        await self._insert_daily_engagement(
            insight_pool, num_days=13, engaged=False, reference_now=now
        )
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        await insight_pool.execute(
            """INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
            VALUES ($1::uuid, $2, TRUE)""",
            str(uuid.uuid4()),
            today_midnight - timedelta(hours=12),
        )
        assert await check_total_disengagement_auto_off(insight_pool, now=now) is False

    async def test_auto_off_triggered_with_and_without_notify(self, insight_pool):
        """auto-off fires after 14 days zero engagement; uses canonical message."""
        from butlers.tools.switchboard.insight.broker import (
            _AUTO_OFF_MESSAGE,
            check_total_disengagement_auto_off,
            get_insight_settings,
        )

        now = datetime.now(UTC)
        await self._insert_daily_engagement(
            insight_pool, num_days=14, engaged=False, reference_now=now
        )

        notify_mock = AsyncMock(return_value={"status": "ok"})
        triggered = await check_total_disengagement_auto_off(
            insight_pool, now=now, notify_fn=notify_mock
        )
        assert triggered is True
        settings = await get_insight_settings(insight_pool)
        assert settings["verbosity"] == "off"
        notify_mock.assert_called_once()
        assert notify_mock.call_args[0][0] == _AUTO_OFF_MESSAGE

    async def test_auto_off_without_notify_fn_still_updates_verbosity(self, insight_pool):
        """auto-off updates verbosity even when no notify_fn is provided."""
        from butlers.tools.switchboard.insight.broker import (
            check_total_disengagement_auto_off,
            get_insight_settings,
        )

        now = datetime.now(UTC)
        await self._insert_daily_engagement(
            insight_pool, num_days=14, engaged=False, reference_now=now
        )
        triggered = await check_total_disengagement_auto_off(insight_pool, now=now, notify_fn=None)
        assert triggered is True
        assert (await get_insight_settings(insight_pool))["verbosity"] == "off"

    async def test_auto_off_window_boundary_excludes_today(self, insight_pool):
        """Today's partial day excluded from window; 14 complete days still trigger;
        13 complete + today's row do not.
        """
        from butlers.tools.switchboard.insight.broker import (
            check_total_disengagement_auto_off,
            get_insight_settings,
        )

        now = datetime.now(UTC)
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # 14 complete past days + today's partial row -> still triggers
        await self._insert_daily_engagement(
            insight_pool, num_days=14, engaged=False, reference_now=now
        )
        await insight_pool.execute(
            """INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
            VALUES ($1::uuid, $2, FALSE)""",
            str(uuid.uuid4()),
            today_midnight + timedelta(hours=1),
        )
        triggered = await check_total_disengagement_auto_off(insight_pool, now=now)
        assert triggered is True
        assert (await get_insight_settings(insight_pool))["verbosity"] == "off"

    async def test_auto_off_13_days_plus_today_not_triggered(self, insight_pool):
        """13 complete past days plus today's partial row do not trigger auto-off."""
        from butlers.tools.switchboard.insight.broker import (
            check_total_disengagement_auto_off,
            get_insight_settings,
        )

        now = datetime.now(UTC)
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        await self._insert_daily_engagement(
            insight_pool, num_days=13, engaged=False, reference_now=now
        )
        await insight_pool.execute(
            """INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
            VALUES ($1::uuid, $2, FALSE)""",
            str(uuid.uuid4()),
            today_midnight + timedelta(hours=2),
        )
        assert await check_total_disengagement_auto_off(insight_pool, now=now) is False
        assert (await get_insight_settings(insight_pool))["verbosity"] != "off"


# ===========================================================================
# Category: propose_insight_candidate unit tests (no Docker) [bu-19ti]
# ===========================================================================


class TestProposeInsightCandidateUnit:
    """Unit tests for propose_insight_candidate validation and verbosity gate."""

    def _make_mock_pool(self, verbosity: str = "minimal", custom_budget=None) -> AsyncMock:
        pool = AsyncMock()
        pool.fetchrow.return_value = {
            "id": 1,
            "verbosity": verbosity,
            "custom_budget": custom_budget,
            "quiet_start": None,
            "quiet_end": None,
            "quiet_timezone": None,
            "updated_at": datetime.now(UTC),
        }
        pool.execute.return_value = "INSERT 0 1"
        return pool

    @pytest.mark.parametrize(
        "overrides,match",
        [
            ({"priority": 0}, "priority must be between 1 and 100"),
            ({"priority": 101}, "priority must be between 1 and 100"),
            ({"dedup_key": ""}, "dedup_key is required"),
            ({"dedup_key": "nodots-at-all"}, "dedup_key must match format"),
            ({"message": ""}, "message must be non-empty"),
            ({"message": "   "}, "message must be non-empty"),
            ({"expires_at": "not-a-datetime"}, "ISO 8601"),
        ],
        ids=[
            "priority-0",
            "priority-101",
            "empty-dedup",
            "bad-dedup",
            "empty-msg",
            "whitespace-msg",
            "invalid-expires",
        ],
    )
    async def test_validation_errors(self, overrides, match):
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        pool = AsyncMock()
        base = {
            "origin_butler": "health",
            "priority": 70,
            "category": "health",
            "dedup_key": "health:bp:user-1:2026-w13",
            "message": "test",
            "expires_at": datetime.now(UTC) + timedelta(days=7),
        }
        base.update(overrides)
        result = await propose_insight_candidate(pool, **base)
        assert result["status"] == "error"
        assert match in result["reason"]
        pool.execute.assert_not_called()

    async def test_past_expires_at_returns_error(self):
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        pool = self._make_mock_pool()
        result = await propose_insight_candidate(
            pool,
            origin_butler="health",
            priority=70,
            category="health",
            dedup_key="health:bp:user-1:2026-w13",
            message="valid",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        assert result["status"] == "error"
        assert "expires_at must be in the future" in result["reason"]
        pool.fetchrow.assert_not_called()

    @pytest.mark.parametrize(
        "verbosity,custom_budget",
        [("off", None), ("minimal", 0)],
        ids=["verbosity-off", "custom-budget-zero"],
    )
    async def test_verbosity_gate_returns_filtered(self, verbosity, custom_budget):
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        pool = self._make_mock_pool(verbosity=verbosity, custom_budget=custom_budget)
        result = await propose_insight_candidate(
            pool,
            origin_butler="health",
            priority=70,
            category="health",
            dedup_key="health:bp:user-1:2026-w13",
            message="No BP logged",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        assert result["status"] == "filtered"
        assert result["reason"] == "verbosity is off"
        pool.execute.assert_not_called()

    async def test_valid_submission_with_optional_fields(self):
        """Valid submission accepted; 3/4-segment dedup_keys pass; optional args forwarded."""
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        pool = self._make_mock_pool(verbosity="normal")
        result = await propose_insight_candidate(
            pool,
            origin_butler="finance",
            priority=55,
            category="spending",
            dedup_key="finance:spending:overage:2026-w13",
            message="Over budget this week",
            expires_at=datetime.now(UTC) + timedelta(days=7),
            cooldown_days=3,
            channel="telegram",
            metadata={"amount_over": 150},
        )
        assert result["status"] == "accepted"
        pool.execute.assert_called_once()
        call_args = pool.execute.call_args.args
        assert call_args[5] == 3
        assert call_args[8] == "telegram"

        # 3-segment dedup_key also valid
        pool2 = self._make_mock_pool()
        r2 = await propose_insight_candidate(
            pool2,
            origin_butler="relationship",
            priority=75,
            category="birthday",
            dedup_key="birthday:entity-123:2026",
            message="Alice's birthday",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        assert r2["status"] == "accepted"


# ===========================================================================
# Category: Daemon job handler registration [bu-a3wr]
# ===========================================================================


class TestDaemonInsightDeliveryJobHandler:
    """insight_delivery_cycle job is registered and callable in the daemon registry."""

    def test_insight_delivery_cycle_registered_and_callable(self):
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        switchboard_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("switchboard", {})
        assert "insight_delivery_cycle" in switchboard_jobs
        assert callable(switchboard_jobs["insight_delivery_cycle"])


# ===========================================================================
# Category: Delivery path spec coverage [bu-dl98i.3.2]
# Verifies the five delivery paths against the spec's
# "Failed Delivery Handling" + "Engagement Tracking" requirements.
# ===========================================================================


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestInsightDeliveryPathsSpec:
    """Five spec-required delivery paths for Failed Delivery Handling + Engagement Tracking.

    Derived from openspec/specs/insight-delivery/spec.md:
      1. no-candidate  — cycle runs with no pending candidates → clean exit, no notify
      2. duplicate     — already-delivered candidate is not re-selected as pending
      3. delivery-failure — notify error → status stays 'pending'; eligible for next cycle
      4. 3-consecutive-failure → candidate marked 'filtered'; no cooldown recorded
      5. success       — full path: propose → deliver → engagement row (engaged=FALSE)
    """

    # -----------------------------------------------------------------------
    # Case 1: no-candidate
    # -----------------------------------------------------------------------

    async def test_no_candidate_cycle_exits_cleanly(self, insight_pool):
        """Case 1 (no-candidate): cycle with empty candidates table returns clean result.

        No delivery, no error, notify_fn is never called.
        """
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        notify_mock = AsyncMock(return_value={"status": "sent"})
        result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        assert result["delivered"] == [], "no candidates → nothing should be delivered"
        assert result["skipped"] is False, "cycle should not report skipped (not quiet hours)"
        assert not notify_mock.called, "notify_fn must not be called when there are no candidates"

    # -----------------------------------------------------------------------
    # Case 2: duplicate / already delivered
    # -----------------------------------------------------------------------

    async def test_already_delivered_candidate_not_redelivered(self, insight_pool):
        """Case 2 (duplicate): a candidate with status='delivered' is excluded from delivery.

        The cycle's candidate query filters for status='pending' only.
        A previously-delivered row must never be re-selected or re-delivered.
        """
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        # Insert a candidate that is already in the 'delivered' state
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message,
                 status, delivered_at)
            VALUES ('health', 80, 'health', 'health:dup-test:user:2026', $1,
                    'Previously delivered insight', 'delivered', now())
            """,
            _future(),
        )

        notify_mock = AsyncMock(return_value={"status": "sent"})
        result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        assert result["delivered"] == [], (
            "already-delivered candidate must not appear in delivered list"
        )
        assert not notify_mock.called, (
            "notify_fn must not be called: there are no pending candidates"
        )

        # The row's status must be unchanged — still 'delivered'
        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:dup-test:user:2026'"
        )
        assert row["status"] == "delivered"

    # -----------------------------------------------------------------------
    # Case 3: delivery-failure
    # -----------------------------------------------------------------------

    async def test_delivery_failure_leaves_candidate_pending_and_retryable(self, insight_pool):
        """Case 3 (delivery-failure): notify error → status stays 'pending'.

        Spec (Failed Delivery Handling, Notify call failure):
        - WHEN notify() returns status='error'
        - THEN candidate status SHALL remain 'pending'
        - AND candidate is eligible for delivery in the next cycle (subject to expiry)
        """
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
            priority=80,
            category="health",
            dedup_key="health:delivery-fail:user:2026",
            message="Blood pressure reminder",
            expires_at=_future(),
        )

        async def _failing_notify(message, metadata):
            return {"status": "error", "error": "channel unavailable"}

        # First cycle: delivery attempt fails
        result = await delivery_cycle(insight_pool, notify_fn=_failing_notify)

        assert result["delivered"] == [], "failed delivery must not appear in delivered list"

        row = await insight_pool.fetchrow(
            "SELECT status, delivery_attempt_count FROM insight_candidates "
            "WHERE dedup_key = 'health:delivery-fail:user:2026'"
        )
        # Spec: status SHALL remain 'pending'
        assert row["status"] == "pending", (
            "spec (Failed Delivery Handling): on notify error status must stay 'pending'"
        )
        # Count incremented → eligible for retry next cycle
        assert row["delivery_attempt_count"] == 1, (
            "delivery_attempt_count must be incremented on failure"
        )

        # Second cycle: still eligible (count < 3); another failure increments again
        result2 = await delivery_cycle(insight_pool, notify_fn=_failing_notify)
        assert result2["delivered"] == []

        row2 = await insight_pool.fetchrow(
            "SELECT status, delivery_attempt_count FROM insight_candidates "
            "WHERE dedup_key = 'health:delivery-fail:user:2026'"
        )
        assert row2["status"] == "pending", "candidate must still be pending after second failure"
        assert row2["delivery_attempt_count"] == 2

    # -----------------------------------------------------------------------
    # Case 4: 3-consecutive-failure → filtered
    # -----------------------------------------------------------------------

    async def test_three_consecutive_failures_mark_candidate_filtered_no_cooldown(
        self, insight_pool
    ):
        """Case 4 (3-consecutive-failure): third failure marks candidate 'filtered'.

        Spec (Failed Delivery Handling, Repeated delivery failure):
        - WHEN a candidate fails delivery on 3 consecutive cycles
        - THEN it SHALL be marked status='filtered' with metadata indicating delivery failure
        - AND no cooldown SHALL be recorded (the insight was never delivered)
        """
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        # Pre-seed with 2 prior failures so the next failure is the 3rd
        await insight_pool.execute(
            """
            INSERT INTO insight_candidates
                (origin_butler, priority, category, dedup_key, expires_at, message,
                 status, delivery_attempt_count)
            VALUES ('health', 80, 'health', 'health:triple-fail:user:2026', $1,
                    'Triple failure insight', 'pending', 2)
            """,
            _future(),
        )

        async def _failing_notify(message, metadata):
            return {"status": "error", "error": "channel permanently unavailable"}

        await delivery_cycle(insight_pool, notify_fn=_failing_notify)

        row = await insight_pool.fetchrow(
            "SELECT status, delivery_attempt_count, metadata FROM insight_candidates "
            "WHERE dedup_key = 'health:triple-fail:user:2026'"
        )
        # Spec: SHALL be marked status='filtered'
        assert row["status"] == "filtered", (
            "spec (Repeated delivery failure): 3 consecutive failures must set status='filtered'"
        )
        assert row["delivery_attempt_count"] == 3

        # Spec: SHALL include metadata indicating delivery failure
        meta = row["metadata"]
        assert meta is not None, (
            "spec (Repeated delivery failure): filtered row must have metadata indicating delivery failure"
        )
        assert meta.get("delivery_failure") is True, (
            "spec: metadata['delivery_failure'] must be True when filtered due to repeated failures"
        )
        assert meta.get("failed_attempts") == 3, (
            "spec: metadata['failed_attempts'] must equal delivery_attempt_count (3)"
        )

        # Spec: no cooldown SHALL be recorded (insight was never delivered)
        cooldown = await insight_pool.fetchrow(
            "SELECT * FROM insight_cooldowns WHERE dedup_key = 'health:triple-fail:user:2026'"
        )
        assert cooldown is None, (
            "spec: no cooldown must be recorded when a candidate is filtered due to delivery failure"
        )

    # -----------------------------------------------------------------------
    # Case 5: success — full path with engagement tracking
    # -----------------------------------------------------------------------

    async def test_success_propose_queue_deliver_engagement_tracked(self, insight_pool):
        """Case 5 (success): full delivery path — propose → queue → deliver → engage.

        Spec (Engagement Tracking, Engagement row creation):
        - WHEN an insight (standalone or digest) is delivered
        - THEN one engagement tracking row SHALL be created per delivered candidate
          in public.insight_engagement with insight_id, delivered_at, and engaged=FALSE
        """
        from butlers.tools.switchboard.insight.broker import (
            delivery_cycle,
            propose_insight_candidate,
        )

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'
        """)

        # Step 1: Propose candidate (simulates butler insight-scan output)
        propose_result = await propose_insight_candidate(
            insight_pool,
            origin_butler="relationship",
            priority=85,
            category="birthday",
            dedup_key="birthday:spec-path:2026",
            message="Alice's birthday is in 3 days",
            expires_at=_future(),
        )
        assert propose_result["status"] == "accepted", (
            "candidate must be accepted before it can enter the delivery queue"
        )

        # Step 2: Run delivery cycle with mock notify (no real notifications)
        notify_mock = AsyncMock(return_value={"status": "sent"})
        cycle_result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        # Delivery must succeed
        assert not cycle_result["skipped"], "cycle must not be skipped"
        assert len(cycle_result["delivered"]) == 1, "exactly one candidate must be delivered"
        assert notify_mock.called, "notify_fn must be called for the delivery"

        # Candidate status → delivered
        row = await insight_pool.fetchrow(
            "SELECT status, delivered_at FROM insight_candidates "
            "WHERE dedup_key = 'birthday:spec-path:2026'"
        )
        assert row["status"] == "delivered"
        assert row["delivered_at"] is not None

        # Step 3: Engagement tracking — spec requires one row per delivered candidate
        delivered_id = cycle_result["delivered"][0]
        engagement = await insight_pool.fetchrow(
            "SELECT insight_id, delivered_at, engaged FROM insight_engagement "
            "WHERE insight_id = $1::uuid",
            delivered_id,
        )
        assert engagement is not None, (
            "spec (Engagement Tracking): "
            "one engagement row SHALL be created per delivered candidate"
        )
        assert engagement["engaged"] is False, (
            "spec: engagement must be initial FALSE (user has not yet responded)"
        )
        assert engagement["delivered_at"] is not None, (
            "spec: delivered_at must be set on the engagement row"
        )
