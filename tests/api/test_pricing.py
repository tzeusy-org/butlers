"""Tests for per-model token pricing configuration."""

from __future__ import annotations

import pytest

from butlers.api.pricing import (
    ModelPricing,
    PricingError,
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
# Default path (integration-level)
# ---------------------------------------------------------------------------


class TestDefaultPath:
    def test_loads_repo_pricing_toml(self):
        """Verify the actual pricing.toml at the repo root loads correctly."""
        cfg = load_pricing()  # uses default path
        assert len(cfg.model_ids) >= 1
