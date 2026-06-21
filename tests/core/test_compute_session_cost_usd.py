"""Unit tests for butlers.core.ingestion_events._compute_session_cost_usd.

Covers both paths and all edge cases (condensed via parametrization):

Pricing path:
- pricing + model + tokens         → estimate_session_cost result
- pricing + model, zero tokens     → falls through to JSONB fallback (0 is falsy)
- pricing + tokens, no/empty model → falls through to JSONB fallback
- pricing available, JSONB present → pricing wins (not JSONB)
- pricing + unknown model          → estimate returns 0.0, falls through (not 0.0 directly)

JSONB fallback:
- float / string / int / zero total_usd → coerced/returned
- total_usd absent / None / cost None / cost string / malformed / list → None

Both absent:
- no cost / missing cost key / empty dict (with and without pricing) → None
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.core.ingestion_events import _compute_session_cost_usd

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pricing(model_id: str = "claude-test", price_per_token: float = 1e-6):
    """Build a minimal PricingConfig with a single model entry."""
    from butlers.api.pricing import ModelPricing, PricingConfig

    return PricingConfig({model_id: ModelPricing(price_per_token, price_per_token * 2)})


def _session(**kwargs: Any) -> dict[str, Any]:
    """Thin builder for session dicts with sane defaults."""
    defaults: dict[str, Any] = {
        "model": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost": None,
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Pricing path: model + tokens → estimate_session_cost
# (input 1e-6/token, output 2e-6/token)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "expected"),
    [
        (1000, 500, 0.002),  # both input+output
        (0, 200, 0.0004),  # output only
        (500, 0, 0.0005),  # input only
    ],
)
def test_pricing_path_computes_cost(input_tokens, output_tokens, expected) -> None:
    """pricing + model + tokens → estimate_session_cost result."""
    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    session = _session(model="claude-test", input_tokens=input_tokens, output_tokens=output_tokens)
    result = _compute_session_cost_usd(session, pricing)
    assert result is not None
    assert abs(result - expected) < 1e-9


def test_pricing_path_wins_over_jsonb() -> None:
    """When pricing succeeds, the JSONB cost column is NOT used."""
    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    session = _session(
        model="claude-test",
        input_tokens=1000,
        output_tokens=500,
        cost={"total_usd": 999.0},  # would be a wildly different value
    )
    result = _compute_session_cost_usd(session, pricing)
    # Should be the pricing-computed 0.002, not the JSONB 999.0
    assert result is not None
    assert abs(result - 0.002) < 1e-9


@pytest.mark.parametrize(
    ("model", "input_tokens", "output_tokens", "jsonb_value", "expected"),
    [
        # estimate_session_cost yields 0.0 for unknown model → falls through to JSONB
        ("unknown-model-xyz", 1000, 500, 0.0314, 0.0314),
        # (0 or 0) token count is falsy → falls through to JSONB
        ("claude-test", 0, 0, 0.042, 0.042),
        # no model → falls through to JSONB
        (None, 1000, 500, 0.007, 0.007),
        # empty-string model → falls through to JSONB
        ("", 1000, 500, 0.003, 0.003),
    ],
)
def test_pricing_path_falls_through_to_jsonb_value(
    model, input_tokens, output_tokens, jsonb_value, expected
) -> None:
    """When the pricing path is unusable, the stored JSONB cost wins (never a direct 0.0)."""
    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    session = _session(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost={"total_usd": jsonb_value},
    )
    result = _compute_session_cost_usd(session, pricing)
    assert result is not None
    assert abs(result - expected) < 1e-9


@pytest.mark.parametrize(
    "model",
    [
        "unknown-model-xyz",  # estimate returns 0.0 → falls through → no JSONB → None
        None,  # no model → falls through → no JSONB → None
    ],
)
def test_pricing_path_falls_through_to_none(model) -> None:
    """pricing path unusable + no JSONB cost → None (estimate's 0.0 is never returned)."""
    pricing = _make_pricing("known-model", price_per_token=1e-6)
    session = _session(model=model, input_tokens=1000, output_tokens=500, cost=None)
    result = _compute_session_cost_usd(session, pricing)
    assert result is None


# ---------------------------------------------------------------------------
# JSONB fallback path (pricing=None)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total_usd", "expected"),
    [
        (0.0125, 0.0125),  # float returned directly
        ("0.0125", 0.0125),  # string coerced to float
        (1, 1.0),  # int coerced to float
        (0, 0.0),  # zero returned (not None)
    ],
)
def test_jsonb_fallback_returns_value(total_usd, expected) -> None:
    """No pricing: numeric/coercible total_usd is returned as a float."""
    session = _session(cost={"total_usd": total_usd})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    "cost",
    [
        {"other_key": 0.5},  # missing total_usd key
        {"total_usd": None},  # total_usd explicitly None
        None,  # cost field is None
        '{"total_usd": 0.5}',  # cost is a raw string, not a dict
        {"total_usd": "not-a-number"},  # malformed → ValueError swallowed
        {"total_usd": [0.5]},  # list → TypeError swallowed
    ],
)
def test_jsonb_fallback_returns_none(cost) -> None:
    """No pricing: absent/None/non-dict/malformed cost yields None."""
    session = _session(cost=cost)
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


# ---------------------------------------------------------------------------
# Both absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("session", "pricing_supplied"),
    [
        (_session(), False),  # no pricing, no cost
        ({"model": "claude-test", "input_tokens": 100, "output_tokens": 50}, False),  # no cost key
        ({}, False),  # empty dict, no pricing
        ({}, True),  # empty dict, pricing supplied → no model, no JSONB
    ],
)
def test_no_cost_returns_none(session, pricing_supplied) -> None:
    """No usable cost source (with or without pricing) → None."""
    pricing = _make_pricing("claude-test", price_per_token=1e-6) if pricing_supplied else None
    result = _compute_session_cost_usd(session, pricing)
    assert result is None
