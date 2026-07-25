"""Tests for DiscretionDispatcher's spend-attribution plumbing (bu-qvnce.12).

Covers:
- call(identity=...) records the per-connector identity as the ledger's
  butler_name, replacing the constructor default ("__discretion__").
- call() without identity falls back to the constructor's butler_name
  (backward compatible with quick_add.py / briefing/prompts.py, which already
  override butler_name at construction time and never pass identity).
- purpose="discretion" is always stamped regardless of identity.
- DiscretionEvaluator.evaluate() forwards its source_name as identity.
- Spend routing rules (bu-m95jq): ``apply_spend_routing_rules`` is wired into
  the model-resolution step so a ``purpose="discretion"`` rule can re-route
  the dispatched model, mirroring Spawner._run()'s integration.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.discretion import DiscretionEvaluator
from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.model_routing import Complexity, QuotaStatus, SpendRoutingResult

pytestmark = pytest.mark.unit

_MODULE = "butlers.connectors.discretion_dispatcher"


def _catalog_result() -> tuple[str, str, list, uuid.UUID, int, str]:
    return ("api", "claude-haiku-4-5-20251001", [], uuid.uuid4(), 30, "specialty")


def _allowed_quota() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


def _make_adapter(result_text: str = "FORWARD", usage: dict | None = None) -> MagicMock:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(
        return_value=(
            result_text,
            [],
            usage if usage is not None else {"input_tokens": 5, "output_tokens": 2},
        )
    )
    return adapter


async def test_call_with_identity_records_per_connector_butler_name() -> None:
    """identity= replaces the constructor default in the ledger write."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    adapter = _make_adapter()

    with (
        patch(
            f"{_MODULE}.resolve_model_with_effective_tier",
            AsyncMock(return_value=_catalog_result()),
        ),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await dispatcher.call("hi", identity="tg:12345")

    assert result == "FORWARD"
    mock_record.assert_awaited_once()
    _, kwargs = mock_record.call_args
    assert kwargs["butler_name"] == "tg:12345"
    assert kwargs["purpose"] == "discretion"
    assert kwargs["session_id"] is None


async def test_call_without_identity_falls_back_to_constructor_butler_name() -> None:
    """No identity= → butler_name stays the constructor default (back-compat)."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)  # default butler_name="__discretion__"
    adapter = _make_adapter()

    with (
        patch(
            f"{_MODULE}.resolve_model_with_effective_tier",
            AsyncMock(return_value=_catalog_result()),
        ),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        await dispatcher.call("hi")

    _, kwargs = mock_record.call_args
    assert kwargs["butler_name"] == "__discretion__"
    assert kwargs["purpose"] == "discretion"


async def test_call_with_explicit_butler_name_and_no_identity_uses_butler_name() -> None:
    """quick_add.py / briefing/prompts.py precedent: explicit butler_name at
    construction, no identity= at call time — stays fully backward compatible.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool, butler_name="briefing-runtime")
    adapter = _make_adapter()

    with (
        patch(
            f"{_MODULE}.resolve_model_with_effective_tier",
            AsyncMock(return_value=_catalog_result()),
        ),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        await dispatcher.call("hi")

    _, kwargs = mock_record.call_args
    assert kwargs["butler_name"] == "briefing-runtime"


async def test_evaluator_forwards_source_name_as_identity() -> None:
    """DiscretionEvaluator.evaluate() passes its source_name through as identity."""
    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value="IGNORE")
    evaluator = DiscretionEvaluator(source_name="tg:98765", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="ambient chatter", weight=0.6)

    assert result.verdict == "IGNORE"
    dispatcher.call.assert_awaited_once()
    _, kwargs = dispatcher.call.call_args
    assert kwargs["identity"] == "tg:98765"


async def test_resolve_model_none_raises_before_any_ledger_write() -> None:
    """No catalog entry for the tier → RuntimeError, no ledger write attempted."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool, complexity_tier=Complexity.SPECIALTY)

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        with pytest.raises(RuntimeError, match="No specialty model configured"):
            await dispatcher.call("hi", identity="tg:1")

    mock_record.assert_not_called()


# ---------------------------------------------------------------------------
# Spend routing rules (bu-m95jq), model SELECTION override
# ---------------------------------------------------------------------------


def _rerouted_result() -> SpendRoutingResult:
    """A SpendRoutingResult re-routing to a different model, with a cap."""
    return SpendRoutingResult(
        resolved=("api", "claude-haiku-cheap", [], uuid.uuid4(), 15),
        max_cost_per_call=0.05,
    )


async def test_call_applies_matching_spend_rule_reroutes_model() -> None:
    """A matching purpose=discretion spend rule re-routes the dispatched model."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    adapter = _make_adapter()
    rerouted = _rerouted_result()
    catalog = _catalog_result()

    with (
        patch(
            f"{_MODULE}.resolve_model_with_effective_tier",
            AsyncMock(return_value=catalog),
        ),
        patch(
            f"{_MODULE}.apply_spend_routing_rules", AsyncMock(return_value=rerouted)
        ) as mock_apply,
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter) as mock_get,
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await dispatcher.call("hi", identity="tg:1")

    assert result == "FORWARD"

    # apply_spend_routing_rules was called with the tier-resolved model, the
    # constructor butler_name, the effective tier, and trigger_source="discretion"
    # (the same literal purpose= value recorded on the ledger below), mirroring
    # Spawner._run()'s call convention.
    mock_apply.assert_awaited_once()
    args, kwargs = mock_apply.call_args
    assert args[0] is pool
    assert args[1] == "__discretion__"
    assert args[2] == "specialty"
    assert args[3] == catalog[:5]
    assert kwargs["trigger_source"] == "discretion"

    # The adapter was invoked with the rule-selected model, not the tier-resolved one.
    mock_get.assert_called_with("api", None)
    _, invoke_kwargs = adapter.invoke.call_args
    assert invoke_kwargs["model"] == "claude-haiku-cheap"

    # Reporting/ledger visibility is unaffected: usage is still recorded with
    # purpose="discretion" and the per-connector identity.
    mock_record.assert_awaited_once()
    _, record_kwargs = mock_record.call_args
    assert record_kwargs["purpose"] == "discretion"
    assert record_kwargs["butler_name"] == "tg:1"
    assert record_kwargs["catalog_entry_id"] == rerouted.resolved[3]


async def test_call_no_matching_rule_keeps_tier_resolved_model() -> None:
    """No matching rule -> apply_spend_routing_rules returns the input unchanged."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    adapter = _make_adapter()
    catalog = _catalog_result()
    unchanged = SpendRoutingResult(resolved=catalog[:5])

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(
            f"{_MODULE}.apply_spend_routing_rules", AsyncMock(return_value=unchanged)
        ) as mock_apply,
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await dispatcher.call("hi")

    assert result == "FORWARD"
    mock_apply.assert_awaited_once()
    _, invoke_kwargs = adapter.invoke.call_args
    assert invoke_kwargs["model"] == catalog[1]
    mock_record.assert_awaited_once()


async def test_call_spend_rule_evaluation_failure_fails_open() -> None:
    """A raising apply_spend_routing_rules must not block the discretion call
    (fail-open), keeping the tier-resolved model.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    adapter = _make_adapter()
    catalog = _catalog_result()

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(
            f"{_MODULE}.apply_spend_routing_rules",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await dispatcher.call("hi")

    assert result == "FORWARD"
    _, invoke_kwargs = adapter.invoke.call_args
    assert invoke_kwargs["model"] == catalog[1]
    mock_record.assert_awaited_once()


async def test_call_unpatched_apply_spend_routing_rules_fails_open_on_mock_pool() -> None:
    """End-to-end sanity: the real apply_spend_routing_rules against a plain
    MagicMock pool (no real DB) fails open internally and the call still
    succeeds with the tier-resolved model unchanged, matching every other
    test in this module that never patches apply_spend_routing_rules.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    adapter = _make_adapter()
    catalog = _catalog_result()

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await dispatcher.call("hi")

    assert result == "FORWARD"
    _, invoke_kwargs = adapter.invoke.call_args
    assert invoke_kwargs["model"] == catalog[1]
    mock_record.assert_awaited_once()
