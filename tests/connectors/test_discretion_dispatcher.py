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

import asyncio
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.discretion import DiscretionEvaluator
from butlers.connectors.discretion_dispatcher import (
    PURPOSE_LANE_PRIVATE_CONTENT,
    DiscretionDispatcher,
)
from butlers.core.model_call import SpendCeilingReached
from butlers.core.model_routing import (
    CEILING_DENIAL_REASON_PREFIX,
    CeilingStatus,
    Complexity,
    QuotaStatus,
    SpendRoutingResult,
)

pytestmark = pytest.mark.unit

_MODULE = "butlers.connectors.discretion_dispatcher"
_KERNEL = "butlers.core.model_call"


def _catalog_result() -> tuple[str, str, list, uuid.UUID, int, str]:
    return ("api", "claude-haiku-4-5-20251001", [], uuid.uuid4(), 30, "specialty")


def _allowed_quota() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


def _resolver_with_receipt(catalog):
    async def _resolve(*_args, **kwargs):
        receipt = MagicMock()
        receipt.describe.return_value = {
            "policy_version": "2",
            "winner": {
                "catalog_entry_id": str(catalog[3]),
                "runtime_type": catalog[0],
                "model_id": catalog[1],
                "effective_tier": catalog[5],
                "reason": "sole_candidate",
            },
            "candidates": [
                {
                    "catalog_entry_id": str(catalog[3]),
                    "runtime_type": catalog[0],
                    "model_id": catalog[1],
                    "effective_tier": catalog[5],
                    "outcome": "selected",
                    "exclusion": None,
                    "exclusions": [],
                }
            ],
        }
        kwargs["receipt_sink"].append(receipt)
        return catalog

    return _resolve


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


def test_explicit_credential_authority_is_forwarded_to_runtime_adapter() -> None:
    """A connector can make its public credential authority explicit.

    The dispatcher must not construct a store from an arbitrary schema-local
    model pool, because that would let a local Codex row shadow dashboard auth.
    """
    pool = MagicMock()
    codex_auth_authority = MagicMock()
    adapter = MagicMock()
    dispatcher = DiscretionDispatcher(
        pool=pool,
        codex_auth_authority=codex_auth_authority,
    )

    with patch("butlers.core.runtimes.base.create_adapter", return_value=adapter) as create:
        assert dispatcher._get_or_create_adapter("codex") is adapter

    create.assert_called_once_with(
        "codex",
        butler_name="__discretion__",
        credential_store=codex_auth_authority,
    )


def test_schema_local_dispatcher_does_not_guess_a_credential_authority() -> None:
    """REQ-core-credentials-001: a bare dispatcher cannot shadow public auth."""
    pool = MagicMock()
    adapter = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)

    with patch("butlers.core.runtimes.base.create_adapter", return_value=adapter) as create:
        dispatcher._get_or_create_adapter("codex")

    # The registry passes Codex an explicit ``credential_store=None``: no authority is guessed,
    # and the generic store is never borrowed (bu-m0vjeb.1).
    create.assert_called_once_with(
        "codex",
        butler_name="__discretion__",
        credential_store=None,
    )


def test_codex_dispatcher_ignores_generic_local_credential_store() -> None:
    """REQ-core-credentials-001: generic adapter credentials are not Codex authority."""
    pool = MagicMock()
    local_only_store = MagicMock()
    adapter = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool, credential_store=local_only_store)

    with patch("butlers.core.runtimes.base.create_adapter", return_value=adapter) as create:
        dispatcher._get_or_create_adapter("codex")

    # The registry passes Codex an explicit ``credential_store=None``: no authority is guessed,
    # and the generic store is never borrowed (bu-m0vjeb.1).
    create.assert_called_once_with(
        "codex",
        butler_name="__discretion__",
        credential_store=None,
    )


def test_codex_dispatcher_keeps_authority_when_provider_config_is_present() -> None:
    """REQ-core-credentials-001: generic factory fallback cannot strip Codex authority."""
    pool = MagicMock()
    codex_auth_authority = MagicMock()
    adapter = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool, codex_auth_authority=codex_auth_authority)

    with patch("butlers.core.runtimes.base.create_adapter", return_value=adapter) as create:
        dispatcher._get_or_create_adapter("codex", {"openai": {"base_url": "test"}})

    create.assert_called_once_with(
        "codex",
        butler_name="__discretion__",
        credential_store=codex_auth_authority,
    )


async def test_call_with_identity_records_per_connector_butler_name() -> None:
    """identity= replaces the constructor default in the ledger write."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    adapter = _make_adapter()
    resolver = AsyncMock(return_value=_catalog_result())

    with (
        patch(f"{_KERNEL}.resolve_model_with_effective_tier", resolver),
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await dispatcher.call("hi", identity="tg:12345")

    assert result == "FORWARD"
    intent = resolver.await_args.kwargs["receipt_intent"]
    assert intent.trigger_class == "discretion"
    assert intent.required_features == frozenset()
    assert adapter.invoke.await_args.kwargs["mcp_servers"] == {}
    mock_record.assert_awaited_once()
    _, kwargs = mock_record.call_args
    assert kwargs["butler_name"] == "tg:12345"
    assert kwargs["purpose"] == "discretion"
    assert kwargs["session_id"] is None
    # bu-hz0g0: the discretion lane never composes a layered prompt, so it
    # passes none of record_token_usage()'s composition/resume kwargs -- the
    # ledger columns land honestly NULL rather than a fabricated 0.
    for composition_kwarg in (
        "base_prompt_tokens",
        "timezone_instruction_tokens",
        "context_preamble_tokens",
        "routing_instructions_tokens",
        "memory_context_tokens",
        "resume_outcome",
    ):
        assert kwargs.get(composition_kwarg) is None


async def test_private_content_uses_normal_catalog_and_content_blind_attribution() -> None:
    """The private lane records provenance without changing model selection."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(
        pool=pool,
        purpose_lane=PURPOSE_LANE_PRIVATE_CONTENT,
    )
    adapter = _make_adapter()
    remote = _catalog_result()
    unchanged = SpendRoutingResult(resolved=remote[:5])
    provider_config = {"remote": {"options": {"baseURL": "https://example.invalid"}}}

    with (
        patch(
            f"{_KERNEL}.resolve_model_with_effective_tier",
            AsyncMock(side_effect=_resolver_with_receipt(remote)),
        ),
        patch(
            f"{_KERNEL}.apply_spend_routing_rules",
            AsyncMock(return_value=unchanged),
        ),
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter) as get_adapter,
        patch.object(
            dispatcher, "_resolve_provider_config", AsyncMock(return_value=provider_config)
        ) as provider_lookup,
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as record_usage,
        patch(f"{_KERNEL}.record_dispatch_attempt", AsyncMock()) as record_attempt,
    ):
        result = await dispatcher.call("private fixture", identity="synthetic-chat")

    assert result == "FORWARD"
    get_adapter.assert_called_once_with(remote[0], provider_config)
    provider_lookup.assert_awaited_once_with(remote[1])
    assert adapter.invoke.await_args.kwargs["model"] == remote[1]
    assert record_usage.await_args.kwargs["purpose"] == PURPOSE_LANE_PRIVATE_CONTENT
    assert record_usage.await_args.kwargs["purpose_lane"] == PURPOSE_LANE_PRIVATE_CONTENT
    assert record_usage.await_args.kwargs["butler_name"] == "__discretion__"
    assert record_attempt.await_args.kwargs["outcome"] == "success"
    assert record_attempt.await_args.kwargs["purpose_lane"] == PURPOSE_LANE_PRIVATE_CONTENT
    receipt = record_attempt.await_args.kwargs["resolution_receipt"]
    assert receipt["winner"]["catalog_entry_id"] == str(remote[3])
    assert [c["outcome"] for c in receipt["candidates"]].count("selected") == 1
    selected = next(c for c in receipt["candidates"] if c["outcome"] == "selected")
    assert selected["catalog_entry_id"] == str(remote[3])
    assert selected["exclusion"] is None


async def test_call_without_identity_falls_back_to_constructor_butler_name() -> None:
    """No identity= → butler_name stays the constructor default (back-compat)."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)  # default butler_name="__discretion__"
    adapter = _make_adapter()

    with (
        patch(
            f"{_KERNEL}.resolve_model_with_effective_tier",
            AsyncMock(return_value=_catalog_result()),
        ),
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as mock_record,
    ):
        await dispatcher.call("hi")

    _, kwargs = mock_record.call_args
    assert kwargs["butler_name"] == "__discretion__"
    assert kwargs["purpose"] == "discretion"


async def test_call_keeps_declared_adapter_setup_allowance_outside_model_timeout() -> None:
    """A credential-aware direct call preserves its provider execution budget."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    adapter = _make_adapter()
    adapter.session_timeout_overhead_s = 0.05
    catalog = (
        "api",
        "claude-haiku-4-5-20251001",
        [],
        uuid.uuid4(),
        0.01,
        "specialty",
    )

    async def _near_deadline_invoke(**kwargs):
        assert kwargs["timeout"] == 0.01
        await asyncio.sleep(0.02)
        return "FORWARD", [], {"input_tokens": 5, "output_tokens": 2}

    adapter.invoke.side_effect = _near_deadline_invoke

    with (
        patch(
            f"{_KERNEL}.resolve_model_with_effective_tier",
            AsyncMock(side_effect=_resolver_with_receipt(catalog)),
        ),
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()),
    ):
        result = await dispatcher.call("hi")

    assert result == "FORWARD"


async def test_call_with_explicit_butler_name_and_no_identity_uses_butler_name() -> None:
    """quick_add.py / briefing/prompts.py precedent: explicit butler_name at
    construction, no identity= at call time — stays fully backward compatible.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool, butler_name="briefing-runtime")
    adapter = _make_adapter()

    with (
        patch(
            f"{_KERNEL}.resolve_model_with_effective_tier",
            AsyncMock(return_value=_catalog_result()),
        ),
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as mock_record,
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
        patch(f"{_KERNEL}.resolve_model_with_effective_tier", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as mock_record,
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
            f"{_KERNEL}.resolve_model_with_effective_tier",
            AsyncMock(side_effect=_resolver_with_receipt(catalog)),
        ),
        patch(
            f"{_KERNEL}.apply_spend_routing_rules", AsyncMock(return_value=rerouted)
        ) as mock_apply,
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter) as mock_get,
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as mock_record,
        patch(f"{_KERNEL}.record_dispatch_attempt", AsyncMock()) as record_attempt,
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
    receipt = record_attempt.await_args.kwargs["resolution_receipt"]
    assert receipt["winner"]["catalog_entry_id"] == str(rerouted.resolved[3])
    assert receipt["winner"]["reason"] == "spend_rule_override"
    assert receipt["selection_override"] == {"reason": "spend_rule_override"}
    assert [c["outcome"] for c in receipt["candidates"]].count("selected") == 1
    assert next(c for c in receipt["candidates"] if c["outcome"] == "selected")["exclusion"] is None


async def test_call_no_matching_rule_keeps_tier_resolved_model() -> None:
    """No matching rule -> apply_spend_routing_rules returns the input unchanged."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    adapter = _make_adapter()
    catalog = _catalog_result()
    unchanged = SpendRoutingResult(resolved=catalog[:5])

    with (
        patch(f"{_KERNEL}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(
            f"{_KERNEL}.apply_spend_routing_rules", AsyncMock(return_value=unchanged)
        ) as mock_apply,
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as mock_record,
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
        patch(f"{_KERNEL}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(
            f"{_KERNEL}.apply_spend_routing_rules",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as mock_record,
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
        patch(f"{_KERNEL}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_KERNEL}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_KERNEL}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await dispatcher.call("hi")

    assert result == "FORWARD"
    _, invoke_kwargs = adapter.invoke.call_args
    assert invoke_kwargs["model"] == catalog[1]
    mock_record.assert_awaited_once()


async def test_evaluator_withholds_bearer_text_from_prompt_log_and_window(caplog) -> None:
    """Codes and reset links never reach the dispatcher, the INFO log or later prompts."""
    import logging

    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value="FORWARD")
    evaluator = DiscretionEvaluator(source_name="tg:1", dispatcher=dispatcher)

    with caplog.at_level(logging.INFO):
        await evaluator.evaluate("Your code is 482913", weight=0.6, provider_domain="example.com")
        await evaluator.evaluate(
            "Reset: https://example.com/reset?token=synthetic-reset-token", weight=0.6
        )
        await evaluator.evaluate("see you at lunch", weight=0.6)

    prompts = [call.args[0] for call in dispatcher.call.await_args_list]
    assert "[auth-code withheld: example.com]" in prompts[0]
    for secret in ("482913", "synthetic-reset-token"):
        assert all(secret not in prompt for prompt in prompts)
        assert secret not in caplog.text
    assert "[auth-code withheld: example.com]" in caplog.text
    # The window re-sends the placeholder, not the code, to later evaluations.
    assert "[auth-code withheld: example.com]" in prompts[2]

    # Detector failure: fixed marker, evaluation continues with the same verdict.
    with patch("butlers.connectors.discretion.scrub_text", side_effect=RuntimeError("482913")):
        result = await evaluator.evaluate("Your code is 482913", weight=0.6)
    assert result.verdict == "FORWARD"
    assert "[content withheld]" in dispatcher.call.await_args.args[0]
    assert "482913" not in dispatcher.call.await_args.args[0]


async def test_evaluator_bypass_paths_keep_verdicts_and_store_scrubbed_entry() -> None:
    dispatcher = AsyncMock()
    evaluator = DiscretionEvaluator(source_name="tg:1", dispatcher=dispatcher)

    by_weight = await evaluator.evaluate("Your code is 482913", weight=1.0)
    by_channel = await evaluator.evaluate("Your code is 482913", weight=0.1, channel="dashboard")

    assert (by_weight.reason, by_channel.reason) == ("weight-bypass", "channel-bypass")
    dispatcher.call.assert_not_called()
    assert all("482913" not in entry.text for entry in evaluator.window.entries)
    assert len(evaluator.window.entries) == 2

    # Group-size bypass: a small group skips the LLM and still stores the scrubbed entry.
    small_group = DiscretionEvaluator(
        source_name="tg:2", dispatcher=dispatcher, group_size_bypass_max=20
    )
    by_group = await small_group.evaluate(
        "Your code is 482913", weight=0.1, participant_count=5, chat_type="group"
    )
    assert by_group.reason == "group-size-bypass"
    dispatcher.call.assert_not_called()
    assert "482913" not in small_group.window.entries[0].text


async def test_evaluator_leaves_ordinary_numbers_unchanged() -> None:
    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value="FORWARD")
    evaluator = DiscretionEvaluator(source_name="tg:1", dispatcher=dispatcher)

    await evaluator.evaluate("Order 482913 total 20.50", weight=0.6)

    assert "Order 482913 total 20.50" in dispatcher.call.await_args.args[0]


@contextmanager
def _ceiling_reached(dispatcher: DiscretionDispatcher, adapter: MagicMock):
    """Patch the kernel seam so the owner's monthly ceiling is reached ($51 of $50)."""
    catalog = _catalog_result()
    with (
        patch(
            f"{_KERNEL}.resolve_model_with_effective_tier",
            AsyncMock(side_effect=_resolver_with_receipt(catalog)),
        ),
        patch(
            f"{_KERNEL}.apply_spend_routing_rules",
            AsyncMock(return_value=SpendRoutingResult(resolved=catalog[:5])),
        ),
        patch(
            f"{_KERNEL}.check_monthly_ceiling",
            AsyncMock(return_value=CeilingStatus(allowed=False, mtd_usd=51.0, ceiling_usd=50.0)),
        ),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch(f"{_KERNEL}.record_dispatch_attempt", AsyncMock()) as record_attempt,
    ):
        yield record_attempt


async def test_monthly_ceiling_refuses_a_discretion_call_before_any_invocation() -> None:
    """bu-svqpzo.14 [decision]: the owner's monthly ceiling binds discretion too."""
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    adapter = _make_adapter()

    with (
        _ceiling_reached(dispatcher, adapter) as record_attempt,
        pytest.raises(SpendCeilingReached) as raised,
    ):
        await dispatcher.call("hello", identity="tg:1")

    # The Spawner's refusal text, so both gates read the same in logs and attempt rows.
    expected = f"{CEILING_DENIAL_REASON_PREFIX}: month-to-date $51.00 >= ceiling $50.00"
    assert str(raised.value) == expected
    adapter.invoke.assert_not_awaited()
    row = record_attempt.await_args.kwargs
    assert (row["outcome"], row["failure_reason"], row["produce_fleet_halt"]) == (
        "quota_skip",
        expected,
        True,
    )
    assert row["butler"] == "__discretion__"


@pytest.mark.parametrize(
    ("weight", "verdict", "reason"),
    [
        (0.6, "FORWARD", "fail-open: SpendCeilingReached"),
        (0.3, "IGNORE", "fail-closed: SpendCeilingReached"),
    ],
)
async def test_evaluator_applies_its_weight_default_when_the_ceiling_refuses(
    weight: float, verdict: str, reason: str
) -> None:
    """A ceiling refusal reaches the evaluator as an ordinary error: its default applies."""
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    adapter = _make_adapter()
    evaluator = DiscretionEvaluator(source_name="tg:1", dispatcher=dispatcher)

    with _ceiling_reached(dispatcher, adapter):
        result = await evaluator.evaluate("Dinner at 7?", weight=weight)

    assert (result.verdict, result.reason, result.is_fail_open) == (
        verdict,
        reason,
        verdict == "FORWARD",
    )
    adapter.invoke.assert_not_awaited()
