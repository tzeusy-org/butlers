"""Qwen3.7 Plus tier boundary on OpenCode Go (bu-jkhucb).

The published card (https://opencode.ai/docs/go/) labels the tiers "Qwen3.7 Plus (<= 256K tokens)"
and "Qwen3.7 Plus (> 256K tokens)": exactly 256K tokens is still the lower tier, and the upper tier
starts one token above it. ``TieredModelPricing.tier_for_context`` picks a tier when
``context_tokens >= context_threshold``, so the upper tier's threshold is 256001.
"""

from __future__ import annotations

import pytest

from butlers.core.pricing import TieredModelPricing, load_pricing

pytestmark = pytest.mark.unit

_MODEL = "opencode-go/qwen3.7-plus"
_LOWER_COST = pytest.approx(0.40 + 1.60)
_UPPER_COST = pytest.approx(1.20 + 4.80)


def _cost(context_tokens: int) -> float:
    return load_pricing().estimate_cost(_MODEL, 1_000_000, 1_000_000, context_tokens=context_tokens)


def test_the_upper_tier_threshold_is_one_token_above_256k():
    pricing = load_pricing().get_model_pricing(_MODEL)

    assert isinstance(pricing, TieredModelPricing)
    assert [tier.context_threshold for tier in pricing.tiers] == [0, 256_001]


@pytest.mark.parametrize("context_tokens", [0, 255_999, 256_000])
def test_up_to_and_including_256k_bills_the_lower_tier(context_tokens):
    assert _cost(context_tokens) == _LOWER_COST


@pytest.mark.parametrize("context_tokens", [256_001, 256_002, 1_000_000])
def test_above_256k_bills_the_upper_tier(context_tokens):
    assert _cost(context_tokens) == _UPPER_COST


def test_each_tier_carries_its_published_cached_read_price():
    pricing = load_pricing().get_model_pricing(_MODEL)

    assert isinstance(pricing, TieredModelPricing)
    lower, upper = pricing.tiers
    assert lower.cached_input_price_per_token == pytest.approx(0.04 / 1_000_000)
    assert upper.cached_input_price_per_token == pytest.approx(0.12 / 1_000_000)
