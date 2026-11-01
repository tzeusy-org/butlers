"""Repo price card: published tier boundaries and cache-write rates (bu-kkwbpx)."""

from __future__ import annotations

import pytest

from butlers.core.pricing import load_pricing

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("context_tokens", "expected"),
    [
        (99_999, 0.01),
        (100_000, 0.01),  # the published upper card starts ABOVE 100K
        (100_001, 0.05),
    ],
)
def test_haiku_5_5_upper_card_starts_above_100k(context_tokens, expected):
    cost = load_pricing().estimate_cost(
        "claude-haiku-5-5", 100_000, 0, context_tokens=context_tokens
    )
    assert cost == pytest.approx(expected)


def test_gpt_6_1_sol_cache_writes_bill_at_the_published_rate():
    cost = load_pricing().estimate_cost("gpt-6.1-sol", 0, 0, cache_creation_tokens=1_000_000)
    assert cost == pytest.approx(2.5)
