"""Tests for the §5.4 spend-rule savings daily job.

Covers:
- Per-rule savings calculation (unit, seeded calls + known baseline)
- Idempotency (re-running for the same day produces the same result)
- Rules with no action.model are skipped
- Missing pricing / no schemas handled gracefully
- Job registration in the deterministic job registry (switchboard butler)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.api.pricing import ModelPricing, PricingConfig
from butlers.jobs.spend import (
    _cheapest_workhorse_from_pricing,
    _discover_session_schemas,
    compute_spend_rule_savings,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pricing(models: dict[str, tuple[float, float]]) -> PricingConfig:
    """Build a PricingConfig from {model_id: (input_ppt, output_ppt)} mapping."""
    return PricingConfig(
        models={
            mid: ModelPricing(
                input_price_per_token=inp,
                output_price_per_token=out,
            )
            for mid, (inp, out) in models.items()
        }
    )


def _make_pool(
    *,
    rules: list[dict] | None = None,
    baseline_row: dict | None = None,
) -> MagicMock:
    """Build a mock asyncpg pool for the spend job.

    Parameters
    ----------
    rules:
        Rows returned by ``pool.fetch("SELECT id, action …")``.
    baseline_row:
        Row returned by ``pool.fetchrow`` for the baseline model catalog query.
        ``None`` means the catalog query returns None (no baseline from catalog).

    Note: token totals are patched via ``_sum_tokens_for_model`` in tests that
    need non-zero session data, rather than wiring through the full SQL path.
    """
    pool = MagicMock()

    def _fetch_side_effect(sql: str, *args, **kwargs):
        if "information_schema" in sql:
            # Return one dummy schema so _discover_session_schemas doesn't short-circuit
            return [{"table_schema": "general"}]
        if "spend_rules" in sql and "SELECT id" in sql:
            return rules or []
        return []

    async def _async_fetch(sql: str, *args, **kwargs):
        return _fetch_side_effect(sql, *args, **kwargs)

    pool.fetch = AsyncMock(side_effect=_async_fetch)

    def _fetchrow_side_effect(sql: str, *args, **kwargs):
        if "model_catalog" in sql:
            return baseline_row
        return None

    async def _async_fetchrow(sql: str, *args, **kwargs):
        return _fetchrow_side_effect(sql, *args, **kwargs)

    pool.fetchrow = AsyncMock(side_effect=_async_fetchrow)
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Unit tests: _cheapest_workhorse_from_pricing
# ---------------------------------------------------------------------------


def test_cheapest_workhorse_from_pricing_returns_lowest_cost_model():
    pricing = _pricing(
        {
            "expensive-model": (0.00001, 0.00005),
            "cheap-model": (0.000001, 0.000004),
            "mid-model": (0.000003, 0.000015),
        }
    )
    result = _cheapest_workhorse_from_pricing(pricing)
    assert result == "cheap-model"


def test_cheapest_workhorse_from_pricing_returns_none_for_empty_pricing():
    pricing = PricingConfig(models={})
    result = _cheapest_workhorse_from_pricing(pricing)
    assert result is None


# ---------------------------------------------------------------------------
# Unit tests: _discover_session_schemas
# ---------------------------------------------------------------------------


async def test_discover_session_schemas_filters_internal_schemas():
    """Only butler-owned schemas (not public/connector/etc.) are returned."""
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            {"table_schema": "general"},
            {"table_schema": "health"},
        ]
    )
    schemas = await _discover_session_schemas(pool)
    assert schemas == ("general", "health")
    sql = pool.fetch.call_args[0][0]
    assert "information_schema.tables" in sql
    assert "table_name = 'sessions'" in sql


# ---------------------------------------------------------------------------
# Unit tests: compute_spend_rule_savings — per-rule calculation
# ---------------------------------------------------------------------------


async def test_compute_savings_basic_calculation():
    """Rule using cheap model saves vs workhorse baseline."""
    rule_id = uuid.uuid4()
    pricing = _pricing(
        {
            "cheap-model": (0.000001, 0.000004),  # cheap: $0.001 + $0.004 per 1M
            "workhorse-model": (0.000003, 0.000015),  # baseline: $0.003 + $0.015 per 1M
        }
    )

    rules = [{"id": rule_id, "action": {"model": "cheap-model"}}]
    baseline_row = {"model_id": "workhorse-model"}

    pool = _make_pool(rules=rules, baseline_row=baseline_row)

    # 1M input + 1M output tokens for cheap-model over 7 days
    with patch(
        "butlers.jobs.spend._sum_tokens_for_model",
        return_value=(1_000_000, 1_000_000),
    ):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_processed"] == 1
    assert result["rules_updated"] == 1
    assert result["rules_skipped"] == 0
    assert result["errors"] == 0
    assert result["baseline_model"] == "workhorse-model"

    # Verify UPDATE was called with the correct saved_7d value
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args
    sql = call_args[0][0]
    saved_7d = call_args[0][1]

    assert "UPDATE public.spend_rules" in sql
    assert "saved_7d" in sql

    # actual = 1M * 0.000001 + 1M * 0.000004 = 1.0 + 4.0 = 5.0
    # baseline = 1M * 0.000003 + 1M * 0.000015 = 3.0 + 15.0 = 18.0
    # saved_7d = 18.0 - 5.0 = 13.0
    assert saved_7d == pytest.approx(13.0, rel=1e-6)


async def test_compute_savings_negative_when_rule_is_more_expensive():
    """saved_7d can be negative when the rule model costs more than the baseline."""
    rule_id = uuid.uuid4()
    pricing = _pricing(
        {
            "expensive-model": (0.00001, 0.00005),
            "workhorse-model": (0.000003, 0.000015),
        }
    )

    rules = [{"id": rule_id, "action": {"model": "expensive-model"}}]
    baseline_row = {"model_id": "workhorse-model"}

    pool = _make_pool(rules=rules, baseline_row=baseline_row)

    with patch(
        "butlers.jobs.spend._sum_tokens_for_model",
        return_value=(500_000, 200_000),
    ):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_updated"] == 1

    saved_7d = pool.execute.call_args[0][1]
    # actual = 500K * 0.00001 + 200K * 0.00005 = 5.0 + 10.0 = 15.0
    # baseline = 500K * 0.000003 + 200K * 0.000015 = 1.5 + 3.0 = 4.5
    # saved_7d = 4.5 - 15.0 = -10.5
    assert saved_7d == pytest.approx(-10.5, rel=1e-6)


async def test_compute_savings_zero_tokens_means_zero_saved():
    """Rules with no matching sessions produce saved_7d = 0."""
    rule_id = uuid.uuid4()
    pricing = _pricing(
        {
            "cheap-model": (0.000001, 0.000004),
            "workhorse-model": (0.000003, 0.000015),
        }
    )

    rules = [{"id": rule_id, "action": {"model": "cheap-model"}}]
    baseline_row = {"model_id": "workhorse-model"}

    pool = _make_pool(rules=rules, baseline_row=baseline_row)

    # No sessions matching this model — _sum_tokens_for_model returns (0, 0)
    with patch(
        "butlers.jobs.spend._sum_tokens_for_model",
        return_value=(0, 0),
    ):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_updated"] == 1

    saved_7d = pool.execute.call_args[0][1]
    assert saved_7d == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Unit tests: skipping rules with no action.model
# ---------------------------------------------------------------------------


async def test_compute_savings_skips_rule_with_no_model():
    """Rules without action.model are counted as skipped, not errors."""
    rule_id = uuid.uuid4()
    pricing = _pricing({"workhorse-model": (0.000003, 0.000015)})

    rules = [{"id": rule_id, "action": {}}]  # no "model" key
    baseline_row = {"model_id": "workhorse-model"}

    pool = _make_pool(rules=rules, baseline_row=baseline_row)

    with patch("butlers.jobs.spend._sum_tokens_for_model", return_value=(0, 0)):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_processed"] == 1
    assert result["rules_updated"] == 0
    assert result["rules_skipped"] == 1
    assert result["errors"] == 0
    pool.execute.assert_not_called()


async def test_compute_savings_skips_rule_with_empty_action():
    """Rules with empty action dict are skipped gracefully."""
    rule_id = uuid.uuid4()
    pricing = _pricing({"workhorse-model": (0.000003, 0.000015)})

    rules = [{"id": rule_id, "action": "{}"}]  # JSON string form
    baseline_row = {"model_id": "workhorse-model"}

    pool = _make_pool(rules=rules, baseline_row=baseline_row)

    with patch("butlers.jobs.spend._sum_tokens_for_model", return_value=(0, 0)):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_skipped"] == 1


# ---------------------------------------------------------------------------
# Unit tests: no rules / empty table
# ---------------------------------------------------------------------------


async def test_compute_savings_no_rules_returns_zero_counts():
    """No spend rules → all counts are zero, job completes cleanly."""
    pricing = _pricing({"workhorse-model": (0.000003, 0.000015)})
    pool = _make_pool(rules=[], baseline_row={"model_id": "workhorse-model"})

    with patch("butlers.jobs.spend._sum_tokens_for_model", return_value=(0, 0)):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_processed"] == 0
    assert result["rules_updated"] == 0
    assert result["errors"] == 0


# ---------------------------------------------------------------------------
# Unit tests: multiple rules, idempotency
# ---------------------------------------------------------------------------


async def test_compute_savings_multiple_rules_all_updated():
    """All rules with valid action.model are updated independently."""
    id_a, id_b = uuid.uuid4(), uuid.uuid4()
    pricing = _pricing(
        {
            "cheap-model": (0.000001, 0.000004),
            "workhorse-model": (0.000003, 0.000015),
        }
    )
    rules = [
        {"id": id_a, "action": {"model": "cheap-model"}},
        {"id": id_b, "action": {"model": "cheap-model"}},
    ]
    baseline_row = {"model_id": "workhorse-model"}

    pool = _make_pool(rules=rules, baseline_row=baseline_row)

    with patch(
        "butlers.jobs.spend._sum_tokens_for_model",
        return_value=(1_000_000, 1_000_000),
    ):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_processed"] == 2
    assert result["rules_updated"] == 2
    # Both rules should have the same saved_7d (same model, same token volume)
    calls = pool.execute.call_args_list
    assert len(calls) == 2
    assert calls[0][0][1] == pytest.approx(calls[1][0][1], rel=1e-6)


async def test_compute_savings_idempotent():
    """Running the job twice produces identical saved_7d writes."""
    rule_id = uuid.uuid4()
    pricing = _pricing(
        {
            "cheap-model": (0.000001, 0.000004),
            "workhorse-model": (0.000003, 0.000015),
        }
    )
    rules = [{"id": rule_id, "action": {"model": "cheap-model"}}]
    baseline_row = {"model_id": "workhorse-model"}

    pool1 = _make_pool(rules=rules, baseline_row=baseline_row)
    pool2 = _make_pool(rules=rules, baseline_row=baseline_row)

    with patch(
        "butlers.jobs.spend._sum_tokens_for_model",
        return_value=(2_000_000, 1_000_000),
    ):
        result1 = await compute_spend_rule_savings(pool1, pricing=pricing)
        result2 = await compute_spend_rule_savings(pool2, pricing=pricing)

    assert result1["rules_updated"] == result2["rules_updated"] == 1
    saved_1 = pool1.execute.call_args[0][1]
    saved_2 = pool2.execute.call_args[0][1]
    assert saved_1 == pytest.approx(saved_2, rel=1e-12)


# ---------------------------------------------------------------------------
# Unit tests: catalog fallback
# ---------------------------------------------------------------------------


async def test_compute_savings_falls_back_to_pricing_baseline_when_catalog_unavailable():
    """When model_catalog is unavailable, cheapest pricing model is used as baseline."""
    rule_id = uuid.uuid4()
    pricing = _pricing(
        {
            "rule-model": (0.000002, 0.000008),
            "cheapest": (0.0000005, 0.000002),
        }
    )
    rules = [{"id": rule_id, "action": {"model": "rule-model"}}]

    pool = _make_pool(
        rules=rules,
        baseline_row=None,  # catalog returns nothing
    )

    with patch(
        "butlers.jobs.spend._sum_tokens_for_model",
        return_value=(1_000_000, 1_000_000),
    ):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_updated"] == 1
    assert result["baseline_model"] == "cheapest"


# ---------------------------------------------------------------------------
# Integration: job registry entry
# ---------------------------------------------------------------------------


def test_spend_rule_savings_registered_in_switchboard_registry():
    """spend_rule_savings is present in the switchboard job registry."""
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    registry = get_deterministic_schedule_job_registry()
    assert "switchboard" in registry
    assert "spend_rule_savings" in registry["switchboard"]


async def test_spend_rule_savings_registry_handler_is_callable():
    """The registered handler for spend_rule_savings is async-callable."""
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    registry = get_deterministic_schedule_job_registry()
    handler = registry["switchboard"]["spend_rule_savings"]

    pricing = _pricing({"workhorse-model": (0.000003, 0.000015)})
    pool = _make_pool(rules=[], baseline_row={"model_id": "workhorse-model"})

    # Patch load_pricing so the handler doesn't try to read pricing.toml
    with patch("butlers.jobs.spend.load_pricing", return_value=pricing):
        result = await handler(pool, None)

    assert "rules_processed" in result
    assert result["rules_processed"] == 0
