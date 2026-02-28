"""Tests for per-model token pricing configuration."""

from __future__ import annotations

import pytest

from butlers.api.pricing import (
    ModelPricing,
    PricingConfig,
    PricingError,
    estimate_session_cost,
    load_pricing,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_TOML = """\
[models]

[models."claude-sonnet-4-5-20250929"]
input_price_per_token = 0.000003
output_price_per_token = 0.000015

[models."claude-haiku-4-5-20251001"]
input_price_per_token = 0.0000008
output_price_per_token = 0.000004
"""


@pytest.fixture()
def pricing_file(tmp_path):
    """Write a valid pricing.toml and return its path."""
    p = tmp_path / "pricing.toml"
    p.write_text(VALID_TOML)
    return p


@pytest.fixture()
def config(pricing_file):
    """Load a PricingConfig from the valid fixture file."""
    return load_pricing(pricing_file)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


class TestLoadPricing:
    def test_loads_valid_file(self, config):
        assert len(config.model_ids) == 2

    def test_load_from_custom_path(self, pricing_file):
        cfg = load_pricing(path=pricing_file)
        assert "claude-sonnet-4-5-20250929" in cfg.model_ids

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(PricingError, match="not found"):
            load_pricing(tmp_path / "nonexistent.toml")

    def test_corrupt_toml_raises(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("[models\ngarbage!!!")
        with pytest.raises(PricingError, match="Invalid TOML"):
            load_pricing(bad)

    def test_missing_models_section_raises(self, tmp_path):
        p = tmp_path / "empty.toml"
        p.write_text("[other]\nfoo = 1\n")
        with pytest.raises(PricingError, match="Missing or invalid .models. section"):
            load_pricing(p)

    def test_missing_price_field_raises(self, tmp_path):
        p = tmp_path / "partial.toml"
        p.write_text('[models]\n[models."m1"]\ninput_price_per_token = 0.001\n')
        with pytest.raises(PricingError, match="Missing required field"):
            load_pricing(p)

    def test_invalid_price_value_raises(self, tmp_path):
        p = tmp_path / "badval.toml"
        p.write_text(
            '[models]\n[models."m1"]\n'
            'input_price_per_token = "not-a-number"\n'
            "output_price_per_token = 0.001\n"
        )
        with pytest.raises(PricingError, match="Invalid price value"):
            load_pricing(p)

    def test_model_entry_not_table_raises(self, tmp_path):
        p = tmp_path / "scalar.toml"
        p.write_text('[models]\n"bad-model" = 42\n')
        with pytest.raises(PricingError, match="Expected table"):
            load_pricing(p)

    def test_valid_custom_pricing(self, tmp_path):
        toml_file = tmp_path / "pricing.toml"
        toml_file.write_text(
            "[models]\n"
            '[models."my-model"]\n'
            "input_price_per_token = 0.001\n"
            "output_price_per_token = 0.002\n"
        )
        config = load_pricing(toml_file)
        assert "my-model" in config.model_ids
        pricing = config.get_model_pricing("my-model")
        assert pricing.input_price_per_token == 0.001
        assert pricing.output_price_per_token == 0.002


# ---------------------------------------------------------------------------
# get_model_pricing
# ---------------------------------------------------------------------------


class TestGetModelPricing:
    def test_known_model_returns_pricing(self, config):
        pricing = config.get_model_pricing("claude-sonnet-4-5-20250929")
        assert pricing is not None
        assert isinstance(pricing, ModelPricing)
        assert pricing.input_price_per_token == pytest.approx(0.000003)
        assert pricing.output_price_per_token == pytest.approx(0.000015)

    def test_unknown_model_returns_none(self, config):
        assert config.get_model_pricing("nonexistent-model") is None

    def test_model_ids_sorted(self, config):
        ids = config.model_ids
        assert ids == sorted(ids)

    def test_get_model_pricing_returns_correct_object(self):
        mp = ModelPricing(0.000003, 0.000015)
        cfg = PricingConfig({"claude-sonnet": mp})
        assert cfg.get_model_pricing("claude-sonnet") is mp


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_basic_calculation(self, config):
        # 1000 input tokens at $3/1M + 500 output tokens at $15/1M
        cost = config.estimate_cost(
            "claude-sonnet-4-5-20250929",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost is not None
        # 1000 * 0.000003 + 500 * 0.000015 = 0.003 + 0.0075 = 0.0105
        assert cost == pytest.approx(0.0105)

    def test_zero_tokens(self, config):
        cost = config.estimate_cost(
            "claude-sonnet-4-5-20250929",
            input_tokens=0,
            output_tokens=0,
        )
        assert cost == pytest.approx(0.0)

    def test_unknown_model_returns_none(self, config):
        cost = config.estimate_cost(
            "nonexistent-model",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost is None

    def test_large_token_counts(self, config):
        # 1M input + 1M output for haiku: $0.80 + $4.00 = $4.80
        cost = config.estimate_cost(
            "claude-haiku-4-5-20251001",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        assert cost is not None
        assert cost == pytest.approx(4.80)


# ---------------------------------------------------------------------------
# estimate_session_cost helper
# ---------------------------------------------------------------------------


class TestEstimateSessionCost:
    def test_known_model_returns_correct_cost(self, config):
        # 1000 input * $3/1M + 500 output * $15/1M = 0.003 + 0.0075 = 0.0105
        cost = estimate_session_cost(
            config,
            model_id="claude-sonnet-4-5-20250929",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost == pytest.approx(0.0105)

    def test_unknown_model_returns_zero(self, config):
        cost = estimate_session_cost(
            config,
            model_id="nonexistent-model",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost == 0.0

    def test_zero_tokens(self, config):
        cost = estimate_session_cost(
            config,
            model_id="claude-sonnet-4-5-20250929",
            input_tokens=0,
            output_tokens=0,
        )
        assert cost == pytest.approx(0.0)

    def test_matches_direct_estimate(self, config):
        direct = config.estimate_cost("claude-sonnet-4-5-20250929", 1000, 500)
        helper = estimate_session_cost(config, "claude-sonnet-4-5-20250929", 1000, 500)
        assert direct == helper


# ---------------------------------------------------------------------------
# Default path (integration-level)
# ---------------------------------------------------------------------------


class TestDefaultPath:
    def test_loads_repo_pricing_toml(self):
        """Verify the actual pricing.toml at the repo root loads correctly."""
        cfg = load_pricing()  # uses default path
        assert len(cfg.model_ids) >= 1


# ---------------------------------------------------------------------------
# Dependency injection: init_pricing / get_pricing
# ---------------------------------------------------------------------------


class TestPricingDependency:
    def test_get_pricing_raises_before_init(self):
        """get_pricing raises RuntimeError when called before init_pricing."""
        import butlers.api.deps as deps_mod

        # Ensure clean state
        original = deps_mod._pricing_config
        deps_mod._pricing_config = None
        try:
            with pytest.raises(RuntimeError, match="PricingConfig not initialized"):
                deps_mod.get_pricing()
        finally:
            deps_mod._pricing_config = original

    def test_init_pricing_loads_config(self, pricing_file):
        """init_pricing loads the given pricing.toml and returns a PricingConfig."""
        import butlers.api.deps as deps_mod

        original = deps_mod._pricing_config
        try:
            result = deps_mod.init_pricing(pricing_file)
            assert isinstance(result, PricingConfig)
            assert len(result.model_ids) == 2
        finally:
            deps_mod._pricing_config = original

    def test_get_pricing_returns_config_after_init(self, pricing_file):
        """After init_pricing, get_pricing returns the same PricingConfig."""
        import butlers.api.deps as deps_mod

        original = deps_mod._pricing_config
        try:
            expected = deps_mod.init_pricing(pricing_file)
            actual = deps_mod.get_pricing()
            assert actual is expected
        finally:
            deps_mod._pricing_config = original

    def test_init_pricing_loads_default_repo_toml(self):
        """init_pricing with no path loads the repo-root pricing.toml."""
        import butlers.api.deps as deps_mod

        original = deps_mod._pricing_config
        try:
            result = deps_mod.init_pricing()
            assert isinstance(result, PricingConfig)
            assert len(result.model_ids) >= 1
        finally:
            deps_mod._pricing_config = original
