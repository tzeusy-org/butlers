"""Tests for cost endpoint Pydantic models and ModelPricing dataclass."""

from __future__ import annotations

import pytest

from butlers.api.models import CostSummary, DailyCost, TopSession
from butlers.api.pricing import ModelPricing

pytestmark = pytest.mark.unit


class TestCostModels:
    def test_cost_summary_defaults(self):
        s = CostSummary(
            total_cost_usd=0.0,
            total_sessions=0,
            total_input_tokens=0,
            total_output_tokens=0,
        )
        assert s.by_butler == {}
        assert s.by_model == {}

    def test_cost_summary_with_breakdown(self):
        s = CostSummary(
            total_cost_usd=1.5,
            total_sessions=10,
            total_input_tokens=100000,
            total_output_tokens=50000,
            by_butler={"switchboard": 1.0, "general": 0.5},
            by_model={"claude-sonnet": 1.5},
        )
        assert s.by_butler["switchboard"] == 1.0
        assert sum(s.by_butler.values()) == pytest.approx(s.total_cost_usd)

    def test_cost_summary_json_round_trip(self):
        s = CostSummary(
            total_cost_usd=2.34,
            total_sessions=5,
            total_input_tokens=50000,
            total_output_tokens=25000,
        )
        restored = CostSummary.model_validate_json(s.model_dump_json())
        assert restored == s

    def test_daily_cost_model(self):
        d = DailyCost(
            date="2026-02-10",
            cost_usd=0.45,
            sessions=3,
            input_tokens=10000,
            output_tokens=5000,
        )
        assert d.date == "2026-02-10"
        assert d.cost_usd == 0.45

    def test_daily_cost_json_round_trip(self):
        d = DailyCost(
            date="2026-02-09",
            cost_usd=1.23,
            sessions=7,
            input_tokens=50000,
            output_tokens=25000,
        )
        restored = DailyCost.model_validate_json(d.model_dump_json())
        assert restored == d

    def test_top_session_model(self):
        t = TopSession(
            session_id="abc-123",
            butler="switchboard",
            cost_usd=0.89,
            input_tokens=30000,
            output_tokens=15000,
            model="claude-sonnet-4-5-20250929",
            started_at="2026-02-10T10:30:00Z",
        )
        assert t.butler == "switchboard"
        assert t.model == "claude-sonnet-4-5-20250929"

    def test_top_session_json_round_trip(self):
        t = TopSession(
            session_id="def-456",
            butler="general",
            cost_usd=2.34,
            input_tokens=100000,
            output_tokens=50000,
            model="claude-opus-4-6",
            started_at="2026-02-10T09:00:00Z",
        )
        restored = TopSession.model_validate_json(t.model_dump_json())
        assert restored == t


class TestModelPricingDataclass:
    def test_frozen(self):
        mp = ModelPricing(0.001, 0.002)
        with pytest.raises(AttributeError):
            mp.input_price_per_token = 0.003

    def test_values(self):
        mp = ModelPricing(0.000003, 0.000015)
        assert mp.input_price_per_token == 0.000003
        assert mp.output_price_per_token == 0.000015
