"""Qwen Plus historical tier boundaries on OpenCode Go (bu-jkhucb).

The dated official cards supply the inclusive/exclusive relation, and the same publisher's
structured records supply numeric size 256000 with matching rates. Their joint evidence puts
the first upper-tier integer at 256001 for both models; legacy size alone is not comparator
proof. Qwen3.6's continued availability and actual billing at the source date remain unknown.
"""

from __future__ import annotations

import pytest

from butlers.core.pricing import TieredModelPricing, load_pricing

pytestmark = [
    pytest.mark.unit,
    pytest.mark.parametrize(
        "model", ["opencode-go/qwen3.7-plus", "opencode-go/qwen3.6-plus"], ids=["qwen37", "qwen36"]
    ),
]

_LOWER_COST = {"opencode-go/qwen3.7-plus": 2.0, "opencode-go/qwen3.6-plus": 3.5}
_UPPER_COST = {"opencode-go/qwen3.7-plus": 6.0, "opencode-go/qwen3.6-plus": 8.0}
_CACHE_READ = {"opencode-go/qwen3.7-plus": (0.04, 0.12), "opencode-go/qwen3.6-plus": (0.05, 0.20)}


def _cost(model: str, context_tokens: int) -> float:
    return load_pricing().estimate_cost(model, 1_000_000, 1_000_000, context_tokens=context_tokens)


def test_the_upper_tier_threshold_is_one_token_above_256k(model):
    pricing = load_pricing().get_model_pricing(model)

    assert isinstance(pricing, TieredModelPricing)
    assert [tier.context_threshold for tier in pricing.tiers] == [0, 256_001]


@pytest.mark.parametrize("context_tokens", [0, 255_999, 256_000])
def test_up_to_and_including_256k_bills_the_lower_tier(model, context_tokens):
    assert _cost(model, context_tokens) == pytest.approx(_LOWER_COST[model])


@pytest.mark.parametrize("context_tokens", [256_001, 256_002, 1_000_000])
def test_above_256k_bills_the_upper_tier(model, context_tokens):
    assert _cost(model, context_tokens) == pytest.approx(_UPPER_COST[model])


def test_each_tier_carries_its_published_cached_read_price(model):
    pricing = load_pricing().get_model_pricing(model)

    assert isinstance(pricing, TieredModelPricing)
    lower, upper = pricing.tiers
    lower_rate, upper_rate = _CACHE_READ[model]
    assert lower.cached_input_price_per_token == pytest.approx(lower_rate / 1_000_000)
    assert upper.cached_input_price_per_token == pytest.approx(upper_rate / 1_000_000)
