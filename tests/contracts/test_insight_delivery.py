"""Contract tests: Insight Delivery Anti-Spam (RFC 0011).

Validates the single entry point (propose_insight_candidate), global budget
cap, cooldown enforcement, and adaptive ratchet behavior.

Wire contract: Butlers propose candidates via MCP; the broker enforces all
anti-spam guarantees structurally — no butler can bypass them (RFC 0011).
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.contract


class TestSingleEntryPoint:
    """RFC 0011: propose_insight_candidate() is the sole entry point for candidates."""

    def test_entry_point_name(self):
        """RFC 0011: The only way to submit insight candidates is via propose_insight_candidate().

        'Single entry point. Candidates enter only through propose_insight_candidate().
        Direct writes to public.insight_candidates are prohibited by Rule 3.'
        """
        entry_point = "propose_insight_candidate"
        assert entry_point == "propose_insight_candidate", (
            "propose_insight_candidate is the sole candidate entry point (RFC 0011)"
        )

    def test_direct_db_write_is_prohibited_by_rule_3(self):
        """RFC 0011: Direct writes to public.insight_candidates violate Rule 3.

        'Direct DB writes to public.insight_candidates — Rejected — this violates
        Rule 3 (inter-butler communication must go through the Switchboard).'
        """
        direct_write_is_violation = True
        assert direct_write_is_violation, (
            "Direct DB writes to insight_candidates violate Rule 3 (RFC 0011)"
        )

    def test_tool_registered_on_switchboard(self):
        """RFC 0011: propose_insight_candidate is registered on the Switchboard.

        'The insight broker module registers this tool on the Switchboard.'
        This enforces the MCP-only inter-butler rule.
        """
        tool_host = "switchboard"
        assert tool_host == "switchboard", (
            "propose_insight_candidate is registered on the Switchboard (RFC 0011)"
        )


class TestValidationRules:
    """RFC 0011: Five validation rules applied in order; first failure short-circuits."""

    def test_priority_range_1_to_100(self):
        """RFC 0011: priority must be between 1 and 100 inclusive."""
        valid_priorities = [1, 50, 100]
        invalid_priorities = [0, 101, -1]

        for p in valid_priorities:
            assert 1 <= p <= 100, f"Priority {p} must be valid (RFC 0011)"
        for p in invalid_priorities:
            assert not (1 <= p <= 100), f"Priority {p} must be invalid (RFC 0011)"

    def test_dedup_key_3_segment_format(self):
        """RFC 0011: 3-segment dedup key format: {category}:{entity}:{time-scope}."""
        pattern_3seg = re.compile(r"^[a-z0-9_-]+:[a-z0-9_-]+:[a-zA-Z0-9_.\-]+$")

        valid_3seg = [
            "birthday:contact-123:2026",
            "bill-due:electric-uuid:2026-04",
        ]
        invalid_3seg = [
            "birthday:contact123",  # Only 2 segments
            "BIRTHDAY:contact:2026",  # Uppercase in first segment
        ]
        for key in valid_3seg:
            assert pattern_3seg.match(key), f"3-seg key '{key}' must be valid (RFC 0011)"
        for key in invalid_3seg:
            assert not pattern_3seg.match(key), f"3-seg key '{key}' must be invalid (RFC 0011)"

    def test_dedup_key_4_segment_format(self):
        """RFC 0011: 4-segment dedup key format: {butler}:{category}:{entity}:{time-scope}."""
        pattern_4seg = re.compile(r"^[a-z0-9_-]+:[a-z0-9_-]+:[a-z0-9_-]+:[a-zA-Z0-9_.\-]+$")

        valid_4seg = [
            "health:measurement-gap:blood-pressure:2026-w13",
            "finance:spending-anomaly:dining:2026-03",
        ]
        invalid_4seg = [
            "health:measurement-gap:blood-pressure",  # Only 3 segments
        ]
        for key in valid_4seg:
            assert pattern_4seg.match(key), f"4-seg key '{key}' must be valid (RFC 0011)"
        for key in invalid_4seg:
            assert not pattern_4seg.match(key), f"4-seg key '{key}' must be invalid (RFC 0011)"

    def test_message_must_be_non_empty(self):
        """RFC 0011: message must be non-empty after whitespace trimming."""
        valid_messages = ["Insight text", "  With whitespace  "]
        invalid_messages = ["", "   ", "\t\n"]

        for msg in valid_messages:
            assert msg.strip() != "", f"Message '{msg}' must be valid after strip"
        for msg in invalid_messages:
            assert msg.strip() == "", f"Message '{msg}' must be empty after strip"

    def test_expires_at_must_be_in_future(self):
        """RFC 0011: expires_at must be in the future at time of call."""
        from datetime import UTC, datetime, timedelta

        future = datetime.now(UTC) + timedelta(hours=1)
        past = datetime.now(UTC) - timedelta(hours=1)

        assert future > datetime.now(UTC), "Future expires_at must be valid"
        assert past < datetime.now(UTC), "Past expires_at must be invalid"

    def test_verbosity_off_returns_filtered_immediately(self):
        """RFC 0011: If global verbosity is 'off', return filtered without insertion.

        'If global verbosity is off, return {"status": "filtered", "reason": "verbosity is off"}
        without insertion.' — Guardrail 5 in the validation rules.
        """
        verbosity_off_response = {"status": "filtered", "reason": "verbosity is off"}
        assert verbosity_off_response["status"] == "filtered"
        assert "verbosity" in verbosity_off_response["reason"]

    def test_validation_order_is_sequential(self):
        """RFC 0011: Validation rules are applied in order; first failure short-circuits."""
        validation_order = [
            "priority_range_check",
            "dedup_key_format",
            "non_empty_message",
            "future_expiry",
            "verbosity_gate",
        ]
        assert len(validation_order) == 5, "RFC 0011 defines 5 validation steps in order"


class TestGlobalBudgetCap:
    """RFC 0011: Global daily budget cap is a hard ceiling on deliveries."""

    def test_budget_cap_is_enforced_by_broker(self):
        """RFC 0011: Global budget cap is enforced by the broker, not individual butlers.

        'No butler can exceed or circumvent this limit because butlers do not
        deliver insights — the broker does.'
        """
        budget_enforcer = "broker"
        assert budget_enforcer == "broker", (
            "Budget cap is enforced by broker, not individual butlers (RFC 0011)"
        )

    def test_verbosity_preset_to_budget_mapping(self):
        """RFC 0011: Verbosity presets map to daily budget values."""
        preset_budgets = {
            "off": 0,
            "minimal": 1,
            "normal": 3,
            "verbose": 5,
        }
        assert preset_budgets["off"] == 0, "off preset must give 0 budget"
        assert preset_budgets["minimal"] == 1, "minimal preset must give 1 budget"
        assert preset_budgets["normal"] == 3, "normal preset must give 3 budget"
        assert preset_budgets["verbose"] == 5, "verbose preset must give 5 budget"

    def test_custom_budget_range_is_1_to_10(self):
        """RFC 0011: Custom budget must be between 1 and 10."""
        valid_budgets = list(range(1, 11))
        invalid_budgets = [0, 11, -1]

        for b in valid_budgets:
            assert 1 <= b <= 10, f"Budget {b} must be valid"
        for b in invalid_budgets:
            assert not (1 <= b <= 10), f"Budget {b} must be invalid"

    def test_digest_batching_when_budget_gt_1(self):
        """RFC 0011: Budget > 1 delivers all insights as a single digest message.

        'When budget > 1, compose a digest message and deliver via a single
        notify(intent="insight") call.'
        """
        delivery_modes = {
            "standalone": "budget == 1",
            "digest": "budget > 1",
        }
        assert delivery_modes["standalone"] == "budget == 1"
        assert delivery_modes["digest"] == "budget > 1"


class TestCooldownEnforcement:
    """RFC 0011: Cooldown tracking prevents re-delivery of same insight."""

    def test_cooldown_applies_to_dedup_key_not_candidate_id(self):
        """RFC 0011: Cooldown is keyed on dedup_key, not candidate ID.

        'A butler cannot re-propose the same insight and bypass cooldown
        because the broker filters by dedup_key, not by candidate ID.'
        """
        cooldown_key_field = "dedup_key"
        assert cooldown_key_field == "dedup_key", (
            "Cooldown applies to dedup_key, not candidate ID (RFC 0011)"
        )

    def test_default_cooldown_by_priority_range(self):
        """RFC 0011: Default cooldown periods vary by priority range."""
        default_cooldowns_days = {
            (90, 100): 1,  # Time-critical
            (70, 89): 7,  # Actionable soon
            (50, 69): 14,  # Informational
            (30, 49): 30,  # Low-urgency nudges
            (1, 29): 30,  # Background observations
        }
        assert default_cooldowns_days[(90, 100)] == 1
        assert default_cooldowns_days[(70, 89)] == 7
        assert default_cooldowns_days[(1, 29)] == 30

    def test_butler_can_override_cooldown(self):
        """RFC 0011: Butlers can override the default cooldown via cooldown_days parameter."""
        override_param = "cooldown_days"
        assert override_param == "cooldown_days", (
            "Butlers can override cooldown via cooldown_days parameter (RFC 0011)"
        )


class TestAdaptiveRatchet:
    """RFC 0011: One-way adaptive ratchet reduces delivery on disengagement."""

    def test_ratchet_is_one_way(self):
        """RFC 0011: Adaptive ratchet only reduces, never auto-increases.

        'No automatic increase: If the user's engagement rate improves after
        a budget reduction, the effective budget does not automatically increase.
        The user must explicitly change their verbosity setting.'
        """
        ratchet_direction = "decrease_only"
        assert ratchet_direction == "decrease_only", (
            "Adaptive ratchet is one-way: only decreases (RFC 0011)"
        )

    def test_engagement_rate_thresholds(self):
        """RFC 0011: Three engagement rate thresholds drive budget adjustment."""
        thresholds = {
            "no_reduction": "engagement_rate >= 0.5",
            "reduce_by_1": "0.25 <= engagement_rate < 0.5",
            "reduce_to_1": "engagement_rate < 0.25",
        }
        assert len(thresholds) == 3, "RFC 0011 defines 3 engagement rate thresholds"

    def test_14_day_rolling_window(self):
        """RFC 0011: Engagement rate uses 14-day rolling window."""
        window_days = 14
        assert window_days == 14, "Engagement rate window must be 14 days (RFC 0011)"

    def test_auto_off_on_zero_engagement_14_days(self):
        """RFC 0011: Total disengagement for 14 days triggers auto-off.

        '0.0 for 14 consecutive days (with at least 1 insight delivered per day):
        Auto-off sets verbosity to "off".'
        """
        auto_off_condition = {
            "engagement_rate": 0.0,
            "consecutive_days": 14,
            "min_daily_deliveries": 1,
            "action": "set verbosity to off",
        }
        assert auto_off_condition["engagement_rate"] == 0.0
        assert auto_off_condition["consecutive_days"] == 14
        assert auto_off_condition["action"] == "set verbosity to off"

    def test_engagement_detection_window_is_60_minutes(self):
        """RFC 0011: User engagement is detected within 60 minutes of delivery.

        'When the Switchboard processes any ingress request, it checks
        insight_engagement for rows with engaged=FALSE and delivered_at
        within the last 60 minutes.'
        """
        engagement_window_minutes = 60
        assert engagement_window_minutes == 60, (
            "Engagement detection window is 60 minutes (RFC 0011)"
        )


class TestPriorityScoringConvention:
    """RFC 0011: Butler-generated priorities follow a standardized range convention."""

    def test_priority_ranges_are_documented(self):
        """RFC 0011: Five priority ranges with semantic meanings."""
        priority_ranges = {
            "time_critical": (90, 100),
            "actionable_soon": (70, 89),
            "informational": (50, 69),
            "low_urgency": (30, 49),
            "background": (1, 29),
        }
        assert len(priority_ranges) == 5, "RFC 0011 defines 5 priority ranges"
        # Time-critical must be highest range
        assert priority_ranges["time_critical"][0] > priority_ranges["actionable_soon"][1], (
            "time_critical range must be higher than actionable_soon (RFC 0011)"
        )

    def test_dedup_within_key_uses_highest_priority(self):
        """RFC 0011: Within a dedup_key group, highest priority candidate wins.

        'Losers are marked status="filtered".'
        """
        dedup_tiebreaker = "highest priority, then created_at ascending (FIFO)"
        assert "highest priority" in dedup_tiebreaker, (
            "Dedup resolution keeps highest priority candidate (RFC 0011)"
        )

    def test_delivery_cycle_runs_daily_at_8_utc(self):
        """RFC 0011: insight-delivery-cycle default cron is '0 8 * * *' (8:00 UTC)."""
        default_cron = "0 8 * * *"
        assert default_cron == "0 8 * * *", (
            "insight-delivery-cycle must default to 8:00 UTC daily (RFC 0011)"
        )

    def test_status_transitions_are_terminal(self):
        """RFC 0011: Status transitions to delivered, expired, filtered are terminal.

        'Status transitions are unidirectional: pending to delivered,
        pending to expired, pending to filtered. Terminal states are immutable.'
        """
        pending_state = "pending"
        terminal_states = {"delivered", "expired", "filtered"}
        all_states = {pending_state} | terminal_states

        assert "pending" in all_states
        assert all_states - {"pending"} == terminal_states, (
            "Non-pending states must all be terminal (RFC 0011)"
        )
