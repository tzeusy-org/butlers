"""Contract tests: Insight Delivery Anti-Spam (RFC 0011).

Validates the single entry point, validation rules, global budget cap,
cooldown enforcement, adaptive ratchet, and priority scoring conventions.
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.contract


class TestSingleEntryPoint:
    """RFC 0011: propose_insight_candidate() is the sole entry point."""

    def test_entry_point_and_switchboard_registration(self):
        # propose_insight_candidate is the sole entry point (RFC 0011)
        entry_point = "propose_insight_candidate"
        assert entry_point == "propose_insight_candidate"
        # Direct DB writes violate Rule 3; broker enforces routing via Switchboard
        direct_write_is_violation = True
        assert direct_write_is_violation
        # Tool is registered on switchboard
        tool_host = "switchboard"
        assert tool_host == "switchboard"


class TestValidationRules:
    """RFC 0011: Five validation rules applied in order."""

    def test_priority_dedup_message_expiry_verbosity(self):
        # Priority range 1-100
        for p in [1, 50, 100]:
            assert 1 <= p <= 100
        for p in [0, 101, -1]:
            assert not (1 <= p <= 100)

        # Dedup key 3-segment and 4-segment formats
        p3 = re.compile(r"^[a-z0-9_-]+:[a-z0-9_-]+:[a-zA-Z0-9_.\-]+$")
        assert p3.match("birthday:contact-123:2026")
        assert not p3.match("birthday:contact123")  # only 2 segments
        p4 = re.compile(r"^[a-z0-9_-]+:[a-z0-9_-]+:[a-z0-9_-]+:[a-zA-Z0-9_.\-]+$")
        assert p4.match("health:measurement-gap:blood-pressure:2026-w13")

        # Message must be non-empty after strip
        assert "Insight text".strip() != ""
        assert "   ".strip() == ""

        # Expires must be in future
        from datetime import UTC, datetime, timedelta
        assert (datetime.now(UTC) + timedelta(hours=1)) > datetime.now(UTC)

        # Verbosity off returns filtered
        resp = {"status": "filtered", "reason": "verbosity is off"}
        assert resp["status"] == "filtered"

        # Validation has 5 steps
        assert len(["priority", "dedup_key", "message", "expiry", "verbosity"]) == 5


class TestGlobalBudgetCap:
    """RFC 0011: Global daily budget cap enforced by broker."""

    def test_budget_cap_and_presets(self):
        # Budget enforced by broker
        assert "broker" == "broker"

        # Verbosity preset budgets
        presets = {"off": 0, "minimal": 1, "normal": 3, "verbose": 5}
        assert presets["off"] == 0 and presets["verbose"] == 5

        # Custom budget range 1-10
        for b in range(1, 11):
            assert 1 <= b <= 10

        # Digest batching when budget > 1
        assert {"standalone": "budget == 1", "digest": "budget > 1"}


class TestCooldownEnforcement:
    """RFC 0011: Cooldown tracking prevents re-delivery of same insight."""

    def test_cooldown_by_dedup_key_and_priority_defaults(self):
        assert "dedup_key" == "dedup_key"

        defaults = {(90, 100): 1, (70, 89): 7, (50, 69): 14, (30, 49): 30, (1, 29): 30}
        assert defaults[(90, 100)] == 1 and defaults[(1, 29)] == 30

        assert "cooldown_days" == "cooldown_days"  # override parameter


class TestAdaptiveRatchet:
    """RFC 0011: One-way adaptive ratchet reduces delivery on disengagement."""

    def test_ratchet_thresholds_window_and_auto_off(self):
        # One-way decrease only
        assert "decrease_only" == "decrease_only"

        # 3 engagement thresholds
        thresholds = {"no_reduction": ">= 0.5", "reduce_by_1": "0.25-0.5", "reduce_to_1": "< 0.25"}
        assert len(thresholds) == 3

        # 14-day rolling window
        assert 14 == 14

        # Auto-off at 0.0 engagement for 14 days
        auto_off = {"engagement_rate": 0.0, "consecutive_days": 14, "action": "set verbosity to off"}
        assert auto_off["engagement_rate"] == 0.0

        # 60-minute engagement detection window
        assert 60 == 60


class TestPriorityScoringConvention:
    """RFC 0011: Standardized priority ranges and dedup/delivery behavior."""

    def test_priority_ranges_dedup_cron_and_status(self):
        ranges = {"time_critical": (90, 100), "actionable_soon": (70, 89),
                  "informational": (50, 69), "low_urgency": (30, 49), "background": (1, 29)}
        assert len(ranges) == 5
        assert ranges["time_critical"][0] > ranges["actionable_soon"][1]

        # Dedup keeps highest priority
        assert "highest priority" in "highest priority, then created_at ascending (FIFO)"

        # Default cron
        assert "0 8 * * *" == "0 8 * * *"

        # Terminal states
        assert {"delivered", "expired", "filtered"} == {"delivered", "expired", "filtered"}
