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
[models."cached-model"]
input_price_per_token = 0.000003
cached_input_price_per_token = 0.0000003
cache_creation_price_per_token = 0.00000375
output_price_per_token = 0.000015
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
        assert len(config.model_ids) == 3
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

    @pytest.mark.parametrize(
        "context,expected_threshold",
        [
            (0, 0),
            (100_000, 100_000),
            (200_000, 100_000),
            (1_000_000, 500_000),
        ],
    )
    def test_tier_selection(self, tiered, context, expected_threshold):
        assert tiered.tier_for_context(context).context_threshold == expected_threshold


# ---------------------------------------------------------------------------
# estimate_cost / estimate_session_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_basic_calculation(self, config):
        # 1000 * $3/1M + 500 * $15/1M = $0.003 + $0.0075 = $0.0105
        cost = config.estimate_cost(
            "claude-sonnet-4-5-20250929", input_tokens=1000, output_tokens=500
        )
        assert cost == pytest.approx(0.0105)

    def test_unknown_model_returns_none(self, config):
        assert config.estimate_cost("nonexistent", input_tokens=1000, output_tokens=500) is None

    def test_tiered_low_tier(self, tiered_config):
        # No context → tier 0: 1M*$2.5/1M + 1M*$15/1M = $17.50
        assert tiered_config.estimate_cost("gpt-5.4", 1_000_000, 1_000_000) == pytest.approx(17.50)

    def test_tiered_high_tier(self, tiered_config):
        # context=300K → tier 1: 1M*$5/1M + 1M*$22.5/1M = $27.50
        assert tiered_config.estimate_cost(
            "gpt-5.4", 1_000_000, 1_000_000, context_tokens=300_000
        ) == pytest.approx(27.50)

    def test_tiered_cached_input(self, tiered_config):
        # 1M cached * $0.25/1M = $0.25
        assert tiered_config.estimate_cost(
            "gpt-5.4", 0, 0, cached_input_tokens=1_000_000
        ) == pytest.approx(0.25)

    def test_flat_cache_buckets_priced_at_configured_rates(self, config):
        # 1M cache reads * $0.30/1M + 1M cache writes * $3.75/1M = $4.05
        assert config.estimate_cost(
            "cached-model",
            0,
            0,
            cached_input_tokens=1_000_000,
            cache_creation_tokens=1_000_000,
        ) == pytest.approx(4.05)

    def test_flat_cache_buckets_fall_back_to_input_rate(self, config):
        # No cache rates configured → cached + creation bill at the full
        # input rate ($3/1M), never at $0.
        assert config.estimate_cost(
            "claude-sonnet-4-5-20250929",
            0,
            0,
            cached_input_tokens=1_000_000,
            cache_creation_tokens=1_000_000,
        ) == pytest.approx(6.0)

    def test_tiered_cache_creation_falls_back_to_input_rate(self, tiered_config):
        # gpt-5.4 tier 0 defines no cache_creation price → input rate $2.5/1M.
        assert tiered_config.estimate_cost(
            "gpt-5.4", 0, 0, cache_creation_tokens=1_000_000
        ) == pytest.approx(2.5)

    def test_session_cost_passes_cache_buckets(self, config):
        direct = config.estimate_cost(
            "cached-model",
            1000,
            500,
            cached_input_tokens=2000,
            cache_creation_tokens=300,
        )
        helper = estimate_session_cost(
            config,
            "cached-model",
            1000,
            500,
            cached_input_tokens=2000,
            cache_creation_tokens=300,
        )
        assert direct == helper

    def test_session_cost_unknown_model_remains_unpriced(self, config):
        assert estimate_session_cost(config, "nonexistent", 1000, 500) is None

    def test_explicit_subscription_zero_is_a_known_price(self):
        config = PricingConfig(
            {
                "subscription-model": ModelPricing(
                    0.0,
                    0.0,
                    billing_class="subscription",
                )
            }
        )

        assert estimate_session_cost(config, "subscription-model", 1_000, 500) == 0.0
        assert config.billing_class_for("subscription-model") == "subscription"

    def test_session_cost_matches_direct_estimate(self, config):
        direct = config.estimate_cost("claude-sonnet-4-5-20250929", 1000, 500)
        helper = estimate_session_cost(config, "claude-sonnet-4-5-20250929", 1000, 500)
        assert direct == helper


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


class TestPricingDependency:
    def test_get_pricing_raises_before_init(self, monkeypatch):
        import butlers.api.deps as deps_mod

        # Use monkeypatch so pytest auto-restores the singleton on teardown,
        # preventing leaks to other tests running in the same xdist worker
        # (mirrors the _db_manager isolation pattern from bu-ci857 / PR #2485).
        monkeypatch.setattr(deps_mod, "_pricing_config", None)
        with pytest.raises(RuntimeError, match="PricingConfig not initialized"):
            deps_mod.get_pricing()

    def test_init_and_get_pricing(self, pricing_file, monkeypatch):
        import butlers.api.deps as deps_mod

        # Capture current value so monkeypatch restores it; init_pricing() will
        # overwrite the singleton and must not persist past this test.
        monkeypatch.setattr(deps_mod, "_pricing_config", deps_mod._pricing_config)
        result = deps_mod.init_pricing(pricing_file)
        assert isinstance(result, PricingConfig)
        assert deps_mod.get_pricing() is result

    def test_loads_repo_default_pricing_toml(self):
        cfg = load_pricing()
        assert len(cfg.model_ids) >= 1

    def test_repo_default_claude_models_have_cache_rates(self):
        # Regression guard: cached_input_price_per_token on FLAT entries was
        # previously silently dropped by the parser. Claude models must carry
        # both cache rates (reads 0.1x input, writes 1.25x input).
        cfg = load_pricing()
        entry = cfg.get_model_pricing("claude-sonnet-4-6")
        assert isinstance(entry, ModelPricing)
        assert entry.cached_input_price_per_token == pytest.approx(
            entry.input_price_per_token * 0.1
        )
        assert entry.cache_creation_price_per_token == pytest.approx(
            entry.input_price_per_token * 1.25
        )

    @pytest.mark.parametrize(
        ("model_ids", "short_rates", "long_rates", "short_cost", "long_cost"),
        [
            (
                ("gpt-5.6-sol",),
                (0.000005, 0.0000005, 0.00000625, 0.00003),
                (0.00001, 0.000001, 0.0000125, 0.000045),
                41.75,
                68.50,
            ),
            (
                ("gpt-5.6-terra",),
                (0.000002, 0.0000002, 0.0000025, 0.000012),
                (0.000004, 0.0000004, 0.000005, 0.000018),
                16.70,
                27.40,
            ),
            (
                ("gpt-5.6-luna", "gpt-5.6-luna-high", "gpt-5.6-luna-xhigh"),
                (0.0000002, 0.00000002, 0.00000025, 0.0000012),
                (0.0000004, 0.00000004, 0.0000005, 0.0000018),
                1.67,
                2.74,
            ),
        ],
    )
    def test_repo_default_gpt_5_6_models_use_standard_api_pricing(
        self, model_ids, short_rates, long_rates, short_cost, long_cost
    ):
        # These rates are intentionally API-equivalent rather than the active
        # subscription's $0 marginal cost: the dashboard estimates general
        # token-price usage. Luna reasoning aliases need their own exact keys.
        cfg = load_pricing()
        expected_tiers = ((0, short_rates), (272_001, long_rates))

        for model_id in model_ids:
            entry = cfg.get_model_pricing(model_id)
            assert isinstance(entry, TieredModelPricing)
            assert cfg.billing_class_for(model_id) == "metered"
            assert [tier.context_threshold for tier in entry.tiers] == [0, 272_001]

            for tier, (threshold, expected_rates) in zip(entry.tiers, expected_tiers, strict=True):
                assert tier.context_threshold == threshold
                assert (
                    tier.input_price_per_token,
                    tier.cached_input_price_per_token,
                    tier.cache_creation_price_per_token,
                    tier.output_price_per_token,
                ) == pytest.approx(expected_rates)

            assert cfg.estimate_cost(
                model_id,
                1_000_000,
                1_000_000,
                cached_input_tokens=1_000_000,
                cache_creation_tokens=1_000_000,
                context_tokens=272_000,
            ) == pytest.approx(short_cost)
            assert cfg.estimate_cost(
                model_id,
                1_000_000,
                1_000_000,
                cached_input_tokens=1_000_000,
                cache_creation_tokens=1_000_000,
                context_tokens=272_001,
            ) == pytest.approx(long_cost)
