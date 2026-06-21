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
    _sum_tokens_for_all_models,
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

    Note: token totals are patched via ``_sum_tokens_for_all_models`` in tests
    that need non-zero session data, rather than wiring through the full SQL path.
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
    pool.executemany = AsyncMock()
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
# Unit tests: _sum_tokens_for_all_models
# ---------------------------------------------------------------------------


async def test_sum_tokens_for_all_models_returns_dict_keyed_by_model():
    """A single batched query returns per-model token totals."""
    from datetime import UTC, datetime

    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            {"model": "cheap-model", "total_input": 1_000_000, "total_output": 2_000_000},
            {"model": "workhorse-model", "total_input": 500_000, "total_output": 300_000},
        ]
    )
    result = await _sum_tokens_for_all_models(
        pool,
        schemas=("general", "health"),
        model_ids=frozenset({"cheap-model", "workhorse-model"}),
        since=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert result == {
        "cheap-model": (1_000_000, 2_000_000),
        "workhorse-model": (500_000, 300_000),
    }


async def test_sum_tokens_for_all_models_returns_empty_for_no_schemas():
    """Returns empty dict when no butler schemas exist."""
    from datetime import UTC, datetime

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    result = await _sum_tokens_for_all_models(
        pool,
        schemas=(),
        model_ids=frozenset({"cheap-model"}),
        since=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert result == {}
    pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Unit tests: compute_spend_rule_savings — per-rule calculation
# ---------------------------------------------------------------------------


_SAVINGS_PRICING = _pricing(
    {
        "cheap-model": (0.000001, 0.000004),
        "expensive-model": (0.00001, 0.00005),
        "workhorse-model": (0.000003, 0.000015),  # baseline
    }
)


@pytest.mark.parametrize(
    "rule_model,token_totals,expected_saved",
    [
        # cheap vs workhorse baseline, 1M+1M tokens:
        #   actual=5.0, baseline=18.0 → saved=13.0
        ("cheap-model", {"cheap-model": (1_000_000, 1_000_000)}, 13.0),
        # expensive vs baseline, 500K+200K: actual=15.0, baseline=4.5 → saved=-10.5 (negative)
        ("expensive-model", {"expensive-model": (500_000, 200_000)}, -10.5),
        # no matching sessions → (0,0) fallback → saved=0
        ("cheap-model", {}, 0.0),
    ],
    ids=["basic-positive", "negative-when-more-expensive", "zero-tokens"],
)
async def test_compute_savings_per_rule_math(rule_model, token_totals, expected_saved):
    """saved_7d = baseline_cost - actual_cost per rule (may be positive, negative, or zero)."""
    rule_id = uuid.uuid4()
    rules = [{"id": rule_id, "action": {"model": rule_model}}]
    pool = _make_pool(rules=rules, baseline_row={"model_id": "workhorse-model"})

    with patch(
        "butlers.jobs.spend._sum_tokens_for_all_models",
        return_value=token_totals,
    ):
        result = await compute_spend_rule_savings(pool, pricing=_SAVINGS_PRICING)

    assert result["rules_updated"] == 1
    assert result["baseline_model"] == "workhorse-model"
    pool.executemany.assert_called_once()
    batch = pool.executemany.call_args[0][1]
    assert len(batch) == 1
    assert batch[0][0] == pytest.approx(expected_saved, rel=1e-6, abs=1e-9)


# ---------------------------------------------------------------------------
# Unit tests: skipping rules with no action.model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [{}, "{}"],  # dict with no model key; JSON-string empty action
    ids=["empty-dict-action", "empty-json-string-action"],
)
async def test_compute_savings_skips_rule_with_no_model(action):
    """Rules without action.model are counted as skipped, not errored — no write."""
    rule_id = uuid.uuid4()
    pricing = _pricing({"workhorse-model": (0.000003, 0.000015)})
    rules = [{"id": rule_id, "action": action}]
    pool = _make_pool(rules=rules, baseline_row={"model_id": "workhorse-model"})

    with patch("butlers.jobs.spend._sum_tokens_for_all_models", return_value={}):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_processed"] == 1
    assert result["rules_updated"] == 0
    assert result["rules_skipped"] == 1
    assert result["errors"] == 0
    pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Unit tests: no rules / empty table
# ---------------------------------------------------------------------------


async def test_compute_savings_no_rules_returns_zero_counts():
    """No spend rules → all counts are zero, job completes cleanly."""
    pricing = _pricing({"workhorse-model": (0.000003, 0.000015)})
    pool = _make_pool(rules=[], baseline_row={"model_id": "workhorse-model"})

    with patch("butlers.jobs.spend._sum_tokens_for_all_models", return_value={}):
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
        "butlers.jobs.spend._sum_tokens_for_all_models",
        return_value={"cheap-model": (1_000_000, 1_000_000)},
    ):
        result = await compute_spend_rule_savings(pool, pricing=pricing)

    assert result["rules_processed"] == 2
    assert result["rules_updated"] == 2
    # Both rules should have the same saved_7d (same model, same token volume)
    pool.executemany.assert_called_once()
    batch = pool.executemany.call_args[0][1]
    assert len(batch) == 2
    assert batch[0][0] == pytest.approx(batch[1][0], rel=1e-6)


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
        "butlers.jobs.spend._sum_tokens_for_all_models",
        return_value={"cheap-model": (2_000_000, 1_000_000)},
    ):
        result1 = await compute_spend_rule_savings(pool1, pricing=pricing)
        result2 = await compute_spend_rule_savings(pool2, pricing=pricing)

    assert result1["rules_updated"] == result2["rules_updated"] == 1
    saved_1 = pool1.executemany.call_args[0][1][0][0]
    saved_2 = pool2.executemany.call_args[0][1][0][0]
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
        "butlers.jobs.spend._sum_tokens_for_all_models",
        return_value={"rule-model": (1_000_000, 1_000_000)},
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
