"""Unit tests for butlers.core.ingestion_events._compute_session_cost_usd.

Covers both paths and all edge cases:

Pricing path:
- pricing + model + tokens         → estimate_session_cost result
- pricing + model, zero tokens     → falls through to JSONB fallback (0 is falsy)
- pricing + tokens, no model       → falls through to JSONB fallback
- pricing available, JSONB present → pricing wins (not JSONB)
- pricing + unknown model          → estimate_session_cost returns 0.0

JSONB fallback:
- no pricing + cost dict float     → float value returned
- no pricing + cost dict string    → coerced to float
- no pricing + cost dict int       → coerced to float
- no pricing + total_usd absent    → None
- no pricing + cost is None        → None
- no pricing + cost is a string    → None (not a dict)
- no pricing + total_usd None      → None
- no pricing + total_usd malformed → None (TypeError/ValueError swallowed)

Both absent:
- no pricing + no cost             → None
- pricing + no model + no cost     → None
"""

from __future__ import annotations

from typing import Any

import pytest

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
# Pricing path
# ---------------------------------------------------------------------------


def test_pricing_path_basic() -> None:
    """pricing + model + tokens → estimate_session_cost result."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    # input: 1000 × 1e-6, output: 500 × 2e-6
    # = 0.001 + 0.001 = 0.002
    session = _session(model="claude-test", input_tokens=1000, output_tokens=500)
    result = _compute_session_cost_usd(session, pricing)
    assert result is not None
    assert abs(result - 0.002) < 1e-9


def test_pricing_path_output_only() -> None:
    """pricing + model + output tokens only (no input) → still returns cost."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    # input: 0, output: 200 × 2e-6 = 0.0004
    session = _session(model="claude-test", input_tokens=0, output_tokens=200)
    result = _compute_session_cost_usd(session, pricing)
    assert result is not None
    assert abs(result - 0.0004) < 1e-9


def test_pricing_path_input_only() -> None:
    """pricing + model + input tokens only → returns cost from input alone."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    # input: 500 × 1e-6 = 0.0005, output: 0
    session = _session(model="claude-test", input_tokens=500, output_tokens=0)
    result = _compute_session_cost_usd(session, pricing)
    assert result is not None
    assert abs(result - 0.0005) < 1e-9


def test_pricing_path_wins_over_jsonb() -> None:
    """When pricing succeeds, the JSONB cost column is NOT used."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

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


def test_pricing_path_unknown_model_returns_zero() -> None:
    """pricing + model not in catalog → estimate_session_cost returns 0.0."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("known-model", price_per_token=1e-6)
    session = _session(model="unknown-model-xyz", input_tokens=1000, output_tokens=500)
    result = _compute_session_cost_usd(session, pricing)
    # estimate_session_cost returns 0.0 for unknown models
    assert result == 0.0


def test_pricing_path_zero_tokens_falls_through_to_jsonb() -> None:
    """pricing + model, but both token fields are 0 → falls through to JSONB."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    session = _session(
        model="claude-test",
        input_tokens=0,
        output_tokens=0,
        cost={"total_usd": 0.042},
    )
    result = _compute_session_cost_usd(session, pricing)
    # (0 or 0) is falsy → falls through; JSONB value returned
    assert result is not None
    assert abs(result - 0.042) < 1e-9


def test_pricing_path_no_model_falls_through_to_jsonb() -> None:
    """pricing available but session has no model → falls through to JSONB."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    session = _session(
        model=None,
        input_tokens=1000,
        output_tokens=500,
        cost={"total_usd": 0.007},
    )
    result = _compute_session_cost_usd(session, pricing)
    assert result is not None
    assert abs(result - 0.007) < 1e-9


def test_pricing_path_empty_string_model_falls_through_to_jsonb() -> None:
    """pricing available but model is '' → falls through to JSONB."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    session = _session(
        model="",
        input_tokens=1000,
        output_tokens=500,
        cost={"total_usd": 0.003},
    )
    result = _compute_session_cost_usd(session, pricing)
    assert result is not None
    assert abs(result - 0.003) < 1e-9


def test_pricing_path_no_model_no_jsonb_returns_none() -> None:
    """pricing available, no model, no JSONB cost → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    session = _session(model=None, input_tokens=1000, output_tokens=500, cost=None)
    result = _compute_session_cost_usd(session, pricing)
    assert result is None


# ---------------------------------------------------------------------------
# JSONB fallback path (pricing=None)
# ---------------------------------------------------------------------------


def test_jsonb_fallback_float_value() -> None:
    """No pricing, cost dict has float total_usd → returned directly."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost={"total_usd": 0.0125})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is not None
    assert abs(result - 0.0125) < 1e-9


def test_jsonb_fallback_string_value_coerced() -> None:
    """No pricing, cost dict has string total_usd → coerced to float."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost={"total_usd": "0.0125"})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is not None
    assert abs(result - 0.0125) < 1e-9


def test_jsonb_fallback_int_value_coerced() -> None:
    """No pricing, cost dict has int total_usd → coerced to float."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost={"total_usd": 1})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is not None
    assert abs(result - 1.0) < 1e-9


def test_jsonb_fallback_zero_cost() -> None:
    """No pricing, cost dict has total_usd=0 → 0.0 returned (not None)."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost={"total_usd": 0})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result == 0.0


def test_jsonb_fallback_missing_total_usd_key() -> None:
    """No pricing, cost dict exists but lacks total_usd key → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost={"other_key": 0.5})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


def test_jsonb_fallback_total_usd_is_none() -> None:
    """No pricing, cost dict present but total_usd is explicitly None → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost={"total_usd": None})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


def test_jsonb_fallback_cost_is_none() -> None:
    """No pricing, cost field is None → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost=None)
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


def test_jsonb_fallback_cost_is_string() -> None:
    """No pricing, cost field is a raw string (not a dict) → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    # asyncpg sometimes returns JSONB as a pre-decoded string before _decode_session_row runs;
    # but after decoding cost should be dict. Either way, if it's still a string here → None.
    session = _session(cost='{"total_usd": 0.5}')
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


def test_jsonb_fallback_malformed_total_usd() -> None:
    """No pricing, cost dict has non-numeric total_usd → TypeError/ValueError swallowed → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost={"total_usd": "not-a-number"})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


def test_jsonb_fallback_total_usd_list() -> None:
    """No pricing, total_usd is a list → TypeError swallowed → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session(cost={"total_usd": [0.5]})
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


# ---------------------------------------------------------------------------
# Both absent
# ---------------------------------------------------------------------------


def test_no_pricing_no_cost_returns_none() -> None:
    """No pricing and no cost field → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session = _session()
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


def test_no_pricing_no_cost_field_at_all() -> None:
    """Session dict missing cost key entirely → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    session: dict[str, Any] = {"model": "claude-test", "input_tokens": 100, "output_tokens": 50}
    result = _compute_session_cost_usd(session, pricing=None)
    assert result is None


def test_empty_session_dict() -> None:
    """Completely empty session dict, no pricing → None."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    result = _compute_session_cost_usd({}, pricing=None)
    assert result is None


def test_empty_session_dict_with_pricing() -> None:
    """Completely empty session dict, pricing supplied → None (no model, no JSONB)."""
    from butlers.core.ingestion_events import _compute_session_cost_usd

    pricing = _make_pricing("claude-test", price_per_token=1e-6)
    result = _compute_session_cost_usd({}, pricing)
    assert result is None
