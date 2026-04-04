"""Tests for per-model token pricing configuration.

Condensed from 48 tests to ~18 tests (bu-egmz6).
Keeps: loading (valid/error paths), tiered parsing, tier selection, estimate_cost,
estimate_session_cost helper, dependency injection.
Removes: trivial field-by-field round-trips and duplicate parametrized load tests.
"""

from __future__ import annotations

import pytest

from butlers.api.pricing import (
    ModelPricing,
    PricingConfig,
    PricingError,
    PricingTier,
    TieredModelPricing,
    estimate_session_cost,
    load_pricing,
)

pytestmark = pytest.mark.unit

_VALID_TOML = """\
[models]
[models."claude-sonnet-4-5-20250929"]
input_price_per_token = 0.000003
output_price_per_token = 0.000015
[models."claude-haiku-4-5-20251001"]
input_price_per_token = 0.0000008
output_price_per_token = 0.000004
"""

_TIERED_TOML = """\
[models]
[models."flat-model"]
input_price_per_token = 0.000001
output_price_per_token = 0.000002
[models."gpt-5.4"]
[[models."gpt-5.4".tiers]]
context_threshold = 0
input_price_per_token = 0.0000025
cached_input_price_per_token = 0.00000025
output_price_per_token = 0.000015
[[models."gpt-5.4".tiers]]
context_threshold = 272000
input_price_per_token = 0.000005
cached_input_price_per_token = 0.0000005
output_price_per_token = 0.0000225
"""


@pytest.fixture()
def pricing_file(tmp_path):
    p = tmp_path / "pricing.toml"
    p.write_text(_VALID_TOML)
    return p


@pytest.fixture()
def config(pricing_file):
    return load_pricing(pricing_file)


@pytest.fixture()
def tiered_config(tmp_path):
    p = tmp_path / "pricing.toml"
    p.write_text(_TIERED_TOML)
    return load_pricing(p)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


class TestLoadPricing:
    def test_loads_flat_and_tiered_models(self, config, tiered_config):
        assert len(config.model_ids) == 2
        assert isinstance(config.get_model_pricing("claude-sonnet-4-5-20250929"), ModelPricing)
        assert isinstance(tiered_config.get_model_pricing("gpt-5.4"), TieredModelPricing)
        assert isinstance(tiered_config.get_model_pricing("flat-model"), ModelPricing)

    def test_tiered_parsed_correctly(self, tiered_config):
        pricing = tiered_config.get_model_pricing("gpt-5.4")
        assert len(pricing.tiers) == 2
        assert pricing.tiers[0].context_threshold == 0
        assert pricing.tiers[0].input_price_per_token == pytest.approx(0.0000025)
        assert pricing.tiers[0].cached_input_price_per_token == pytest.approx(0.00000025)
        assert pricing.tiers[1].context_threshold == 272_000

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(PricingError, match="not found"):
            load_pricing(tmp_path / "nonexistent.toml")

    def test_corrupt_toml_raises(self, tmp_path):
        p = tmp_path / "bad.toml"
        p.write_text("[models\ngarbage!!!")
        with pytest.raises(PricingError, match="Invalid TOML"):
            load_pricing(p)

    def test_missing_price_field_raises(self, tmp_path):
        p = tmp_path / "partial.toml"
        p.write_text('[models]\n[models."m1"]\ninput_price_per_token = 0.001\n')
        with pytest.raises(PricingError, match="Missing required field"):
            load_pricing(p)

    def test_empty_tiers_raises(self, tmp_path):
        p = tmp_path / "pricing.toml"
        p.write_text('[models]\n[models."m"]\ntiers = []\n')
        with pytest.raises(PricingError, match="non-empty array"):
            load_pricing(p)

    def test_unknown_model_returns_none(self, config):
        assert config.get_model_pricing("nonexistent-model") is None


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------


class TestTierForContext:
    @pytest.fixture()
    def tiered(self):
        return TieredModelPricing(
            tiers=(
                PricingTier(0, 0.001, 0.002, 0.0001),
                PricingTier(100_000, 0.002, 0.004, 0.0002),
                PricingTier(500_000, 0.004, 0.008, 0.0004),
            )
        )

    @pytest.mark.parametrize("context,expected_threshold", [
        (0, 0),
        (100_000, 100_000),
        (200_000, 100_000),
        (1_000_000, 500_000),
    ])
    def test_tier_selection(self, tiered, context, expected_threshold):
        assert tiered.tier_for_context(context).context_threshold == expected_threshold


# ---------------------------------------------------------------------------
# estimate_cost / estimate_session_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_basic_calculation(self, config):
        # 1000 * $3/1M + 500 * $15/1M = $0.003 + $0.0075 = $0.0105
        cost = config.estimate_cost("claude-sonnet-4-5-20250929", input_tokens=1000, output_tokens=500)
        assert cost == pytest.approx(0.0105)

    def test_unknown_model_returns_none(self, config):
        assert config.estimate_cost("nonexistent", input_tokens=1000, output_tokens=500) is None

    def test_tiered_low_tier(self, tiered_config):
        # No context → tier 0: 1M*$2.5/1M + 1M*$15/1M = $17.50
        assert tiered_config.estimate_cost("gpt-5.4", 1_000_000, 1_000_000) == pytest.approx(17.50)

    def test_tiered_high_tier(self, tiered_config):
        # context=300K → tier 1: 1M*$5/1M + 1M*$22.5/1M = $27.50
        assert tiered_config.estimate_cost("gpt-5.4", 1_000_000, 1_000_000, context_tokens=300_000) == pytest.approx(27.50)

    def test_tiered_cached_input(self, tiered_config):
        # 1M cached * $0.25/1M = $0.25
        assert tiered_config.estimate_cost("gpt-5.4", 0, 0, cached_input_tokens=1_000_000) == pytest.approx(0.25)

    def test_session_cost_unknown_model_returns_zero(self, config):
        assert estimate_session_cost(config, "nonexistent", 1000, 500) == 0.0

    def test_session_cost_matches_direct_estimate(self, config):
        direct = config.estimate_cost("claude-sonnet-4-5-20250929", 1000, 500)
        helper = estimate_session_cost(config, "claude-sonnet-4-5-20250929", 1000, 500)
        assert direct == helper


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


class TestPricingDependency:
    def test_get_pricing_raises_before_init(self):
        import butlers.api.deps as deps_mod
        original = deps_mod._pricing_config
        deps_mod._pricing_config = None
        try:
            with pytest.raises(RuntimeError, match="PricingConfig not initialized"):
                deps_mod.get_pricing()
        finally:
            deps_mod._pricing_config = original

    def test_init_and_get_pricing(self, pricing_file):
        import butlers.api.deps as deps_mod
        original = deps_mod._pricing_config
        try:
            result = deps_mod.init_pricing(pricing_file)
            assert isinstance(result, PricingConfig)
            assert deps_mod.get_pricing() is result
        finally:
            deps_mod._pricing_config = original

    def test_loads_repo_default_pricing_toml(self):
        cfg = load_pricing()
        assert len(cfg.model_ids) >= 1
