"""Tests for DiscretionDispatcher's same-tier failover retry (bu-8fves).

From PR #2936 review: DiscretionDispatcher.call() previously resolved exactly
ONE model-catalog entry, so any adapter.invoke() hiccup (connection error,
provider outage, timeout) degraded straight to DiscretionEvaluator's
weight-based default — unknown-weight senders (0.3 < fail_open 0.5) got a
silent IGNORE on any single-attempt failure, even though Spawner._run() has
had same-tier failover via next_same_tier_candidate all along.

Covers:
- An eligible failure (classifier allow-list match) retries the next
  same-tier candidate and returns its result on success.
- An ineligible failure (business/validation error) raises immediately —
  same as the prior single-attempt behavior — without consulting
  next_same_tier_candidate at all.
- Exhausting every same-tier candidate raises a RuntimeError whose message
  is tagged ``same_tier_failover_exhausted`` so it reads distinctly from a
  single-attempt failure in logs/provenance.
- A quota-denied catalog entry is a pre-invocation same-tier skip: it never
  reaches its adapter, consumes the same attempt cap as a runtime failure,
  and emits bounded non-sensitive operational provenance.
- DiscretionDispatcher reuses the shared
  ``butlers.core.failover_classifier.classify_failover_eligibility`` — it
  does not fork a second, duplicate classifier.
- The classifier call includes ``adapter.last_process_info`` alongside the
  exception, so process-info-gated eligibility checks (e.g. OpenCode's
  pre-tool-call ``APIError`` envelope) are not silently starved.
- End-to-end: when the dispatcher exhausts failover, DiscretionEvaluator's
  pre-existing error-path observability (structured log + a
  ``discretion_evaluations_total`` increment) still fires, so the resulting
  weight-default IGNORE/FORWARD is never silent.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors import discretion_dispatcher as discretion_dispatcher_module
from butlers.connectors.discretion import DiscretionEvaluator, discretion_evaluations_total
from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.failover_classifier import FailoverDecision
from butlers.core.model_routing import QuotaStatus, SpendRoutingResult

pytestmark = pytest.mark.unit

_MODULE = "butlers.connectors.discretion_dispatcher"


def _allowed_quota() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


def _denied_quota() -> QuotaStatus:
    return QuotaStatus(
        allowed=False,
        usage_24h=100,
        limit_24h=100,
        usage_30d=0,
        limit_30d=None,
    )


def _make_adapter(side_effect: list[object]) -> MagicMock:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(side_effect=side_effect)
    return adapter


# ---------------------------------------------------------------------------
# Classifier reuse — no duplicate classifier
# ---------------------------------------------------------------------------


def test_discretion_dispatcher_reuses_shared_failover_classifier() -> None:
    """DiscretionDispatcher must import and call the *same* classifier
    Spawner._run() uses, not a forked/duplicate implementation.
    """
    from butlers.connectors import discretion_dispatcher
    from butlers.core import failover_classifier

    assert (
        discretion_dispatcher.classify_failover_eligibility
        is failover_classifier.classify_failover_eligibility
    )


async def test_call_passes_adapter_process_info_to_classifier() -> None:
    """The classifier call must include ``adapter.last_process_info``.

    Some failover-eligibility gates (e.g. OpenCode's pre-tool-call ``APIError``
    envelope) key off ``process_info`` rather than the exception message alone
    — omitting it would silently misclassify those failures as ineligible.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)

    catalog = ("opencode", "some-opencode-model", [], uuid.uuid4(), 30, "specialty")
    process_info = {"runtime_type": "opencode", "is_pre_tool_call": True}
    adapter = _make_adapter(side_effect=[RuntimeError("boom")])
    adapter.last_process_info = process_info

    captured: list = []

    def _fake_classify(ctx):
        captured.append(ctx)
        return FailoverDecision(eligible=False, reason="test_stub")

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.classify_failover_eligibility", side_effect=_fake_classify),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await dispatcher.call("hi")

    assert len(captured) == 1
    assert captured[0].process_info == process_info


# ---------------------------------------------------------------------------
# Failure -> same-tier retry -> success
# ---------------------------------------------------------------------------


async def test_call_retries_next_same_tier_candidate_on_eligible_failure() -> None:
    """A pre-invocation systemic failure (e.g. a connection error) retries the
    next same-tier candidate and returns its result — the caller sees success.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)

    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first_catalog = ("api", "claude-haiku-4-5-20251001", [], first_id, 30, "specialty")
    second_candidate = ("api", "claude-haiku-fallback", [], second_id, 30)

    adapter = _make_adapter(
        side_effect=[
            RuntimeError("Connection error."),
            ("FORWARD", [], {"input_tokens": 5, "output_tokens": 2}),
        ]
    )

    with (
        patch(
            f"{_MODULE}.resolve_model_with_effective_tier",
            AsyncMock(return_value=first_catalog),
        ),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(
            f"{_MODULE}.next_same_tier_candidate",
            AsyncMock(return_value=second_candidate),
        ) as mock_next,
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await dispatcher.call("hi", identity="tg:1")

    assert result == "FORWARD"
    assert adapter.invoke.await_count == 2

    mock_next.assert_awaited_once()
    args, _ = mock_next.call_args
    # (pool, butler_name, effective_tier, attempted_ids)
    assert args[2] == "specialty"
    assert args[3] == [first_id]

    # The failed first attempt raised before usage was captured — only the
    # successful second attempt records token usage.
    mock_record.assert_awaited_once()
    _, kwargs = mock_record.call_args
    assert kwargs["catalog_entry_id"] == second_id


# ---------------------------------------------------------------------------
# Ineligible failure -> no retry (unchanged terminal behavior)
# ---------------------------------------------------------------------------


async def test_call_does_not_retry_ineligible_failure() -> None:
    """A business/validation error is not failover-eligible (default-closed);
    call() raises immediately without ever consulting next_same_tier_candidate.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)

    catalog = ("api", "claude-haiku-4-5-20251001", [], uuid.uuid4(), 30, "specialty")
    adapter = _make_adapter(side_effect=[ValueError("malformed prompt")])

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.next_same_tier_candidate", AsyncMock()) as mock_next,
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        with pytest.raises(ValueError, match="malformed prompt"):
            await dispatcher.call("hi")

    mock_next.assert_not_called()
    mock_record.assert_not_called()


# ---------------------------------------------------------------------------
# All same-tier candidates exhausted -> tagged RuntimeError
# ---------------------------------------------------------------------------


async def test_call_raises_same_tier_failover_exhausted_when_no_candidates_remain() -> None:
    """When next_same_tier_candidate returns None, call() raises a RuntimeError
    tagged ``same_tier_failover_exhausted`` (distinct from a single-attempt
    failure) chained from the original exception.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)

    catalog = ("api", "claude-haiku-4-5-20251001", [], uuid.uuid4(), 30, "specialty")
    adapter = _make_adapter(side_effect=[RuntimeError("Connection error.")])

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.next_same_tier_candidate", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        with pytest.raises(RuntimeError, match="same_tier_failover_exhausted"):
            await dispatcher.call("hi")

    mock_record.assert_not_called()


# ---------------------------------------------------------------------------
# Quota denial -> same-tier skip -> candidate transition (bu-x82cy)
# ---------------------------------------------------------------------------


async def test_call_skips_quota_denied_selected_candidate_before_invocation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A selected entry with no quota is a bounded pre-invocation skip.

    The selected entry is checked first, never reaches ``adapter.invoke()``,
    and transitions only to the next candidate in the effective tier. The
    skip's operational provenance must not carry caller-controlled text.
    """
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)
    dispatcher._metrics = MagicMock()

    selected_id = uuid.uuid4()
    fallback_id = uuid.uuid4()
    selected_model = "selected-" + "x" * 300
    selected = ("api", selected_model, [], selected_id, 30, "specialty")
    fallback = ("api", "fallback-model", [], fallback_id, 30)
    adapter = _make_adapter(side_effect=[("FORWARD", [], {"input_tokens": 1, "output_tokens": 1})])
    prompt_marker = "prompt-must-not-appear-in-quota-provenance"
    system_marker = "system-must-not-appear-in-quota-provenance"
    identity_marker = "identity-must-not-appear-in-quota-provenance"

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=selected)),
        patch(
            f"{_MODULE}.apply_spend_routing_rules",
            AsyncMock(return_value=SpendRoutingResult(resolved=selected[:5])),
        ),
        patch(
            f"{_MODULE}.check_token_quota",
            AsyncMock(side_effect=[_denied_quota(), _allowed_quota()]),
        ) as mock_quota,
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter) as mock_adapter,
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(
            f"{_MODULE}.next_same_tier_candidate",
            AsyncMock(return_value=fallback),
        ) as mock_next,
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        with caplog.at_level(logging.INFO):
            result = await dispatcher.call(
                prompt_marker,
                system_prompt=system_marker,
                identity=identity_marker,
            )

    assert result == "FORWARD"
    assert [call.args[1] for call in mock_quota.await_args_list] == [selected_id, fallback_id]
    mock_next.assert_awaited_once_with(pool, "__discretion__", "specialty", [selected_id])
    mock_adapter.assert_called_once_with("api", None)
    adapter.invoke.assert_awaited_once()
    assert adapter.invoke.await_args.kwargs["model"] == "fallback-model"
    dispatcher._metrics.record_failover_attempt.assert_called_once_with(
        from_model=selected_model[:256],
        to_model="fallback-model",
        reason="quota_exhausted",
    )

    quota_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("DiscretionDispatcher quota skip")
    ]
    assert len(quota_messages) == 1
    assert selected_model[:256] in quota_messages[0]
    assert selected_model not in quota_messages[0]
    assert "specialty" in quota_messages[0]
    assert "attempt=1" in quota_messages[0]
    assert prompt_marker not in quota_messages[0]
    assert system_marker not in quota_messages[0]
    assert identity_marker not in quota_messages[0]


async def test_call_exhausts_same_tier_when_all_discretion_candidates_are_quota_denied() -> None:
    """All denied candidates finish as same-tier exhaustion without invocation."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)

    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first = ("api", "first-denied-model", [], first_id, 30, "specialty")
    second = ("api", "second-denied-model", [], second_id, 30)
    adapter = _make_adapter(side_effect=[])
    attempted_id_snapshots: list[list[uuid.UUID]] = []

    async def _next_same_tier(*args: object) -> tuple[str, str, list, uuid.UUID, int] | None:
        attempted_id_snapshots.append(list(args[3]))
        return [second, None][len(attempted_id_snapshots) - 1]

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=first)),
        patch(
            f"{_MODULE}.apply_spend_routing_rules",
            AsyncMock(return_value=SpendRoutingResult(resolved=first[:5])),
        ),
        patch(
            f"{_MODULE}.check_token_quota",
            AsyncMock(side_effect=[_denied_quota(), _denied_quota()]),
        ),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(
            f"{_MODULE}.next_same_tier_candidate",
            side_effect=_next_same_tier,
        ),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="same_tier_failover_exhausted"):
            await dispatcher.call("hi")

    adapter.invoke.assert_not_awaited()
    assert attempted_id_snapshots == [[first_id], [first_id, second_id]]


async def test_call_keeps_runtime_failure_and_quota_skip_in_one_same_tier_chain() -> None:
    """A runtime failure followed by a quota skip remains one exact-tier chain."""
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)

    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    third_id = uuid.uuid4()
    first = ("api", "runtime-failure-model", [], first_id, 30, "specialty")
    second = ("api", "quota-denied-model", [], second_id, 30)
    third = ("api", "successful-model", [], third_id, 30)
    adapter = _make_adapter(
        side_effect=[
            RuntimeError("Connection error."),
            ("FORWARD", [], {"input_tokens": 1, "output_tokens": 1}),
        ]
    )
    attempted_id_snapshots: list[list[uuid.UUID]] = []

    async def _next_same_tier(*args: object) -> tuple[str, str, list, uuid.UUID, int]:
        attempted_id_snapshots.append(list(args[3]))
        return [second, third][len(attempted_id_snapshots) - 1]

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=first)),
        patch(
            f"{_MODULE}.apply_spend_routing_rules",
            AsyncMock(return_value=SpendRoutingResult(resolved=first[:5])),
        ),
        patch(
            f"{_MODULE}.check_token_quota",
            AsyncMock(side_effect=[_allowed_quota(), _denied_quota(), _allowed_quota()]),
        ),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(
            f"{_MODULE}.classify_failover_eligibility",
            return_value=FailoverDecision(eligible=True, reason="provider_unavailable:test"),
        ),
        patch(
            f"{_MODULE}.next_same_tier_candidate",
            side_effect=_next_same_tier,
        ),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        result = await dispatcher.call("hi")

    assert result == "FORWARD"
    assert adapter.invoke.await_count == 2
    assert attempted_id_snapshots == [[first_id], [first_id, second_id]]
    assert adapter.invoke.await_args.kwargs["model"] == "successful-model"


async def test_quota_skips_consume_the_existing_same_tier_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A denied candidate consumes a cap slot even though no adapter runs."""
    monkeypatch.setattr(discretion_dispatcher_module, "_MAX_FAILOVER_ATTEMPTS", 2)
    pool = MagicMock()
    dispatcher = DiscretionDispatcher(pool=pool)

    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first = ("api", "first-denied-model", [], first_id, 30, "specialty")
    second = ("api", "second-denied-model", [], second_id, 30)
    adapter = _make_adapter(side_effect=[])
    attempted_id_snapshots: list[list[uuid.UUID]] = []

    async def _next_same_tier(*args: object) -> tuple[str, str, list, uuid.UUID, int]:
        attempted_id_snapshots.append(list(args[3]))
        return second

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=first)),
        patch(
            f"{_MODULE}.apply_spend_routing_rules",
            AsyncMock(return_value=SpendRoutingResult(resolved=first[:5])),
        ),
        patch(
            f"{_MODULE}.check_token_quota",
            AsyncMock(side_effect=[_denied_quota(), _denied_quota()]),
        ),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(
            f"{_MODULE}.next_same_tier_candidate",
            side_effect=_next_same_tier,
        ),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        with pytest.raises(
            RuntimeError, match=r"same_tier_failover_exhausted.*2 attempt\(s\).*safety cap"
        ):
            await dispatcher.call("hi")

    adapter.invoke.assert_not_awaited()
    assert attempted_id_snapshots == [[first_id]]


# ---------------------------------------------------------------------------
# End-to-end: exhausted failover -> weight-default -> observable, not silent
# ---------------------------------------------------------------------------


async def test_evaluator_surfaces_exhausted_failover_as_observable_suppression(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When DiscretionDispatcher.call() exhausts same-tier failover and raises,
    DiscretionEvaluator's existing error-path observability (structured ERROR
    log + a discretion_evaluations_total increment) still fires for the
    resulting weight-default verdict — an unknown-weight sender's suppressed
    inbound message is never silent, even after every same-tier candidate
    has been tried.
    """
    source = "tg:exhaustion-observability-test"

    before = discretion_evaluations_total.labels(
        source=source, verdict="IGNORE", outcome="error"
    )._value.get()

    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(
        side_effect=RuntimeError(
            "same_tier_failover_exhausted: tier=specialty after 5 attempt(s); "
            "last error: RuntimeError: Connection error."
        )
    )
    evaluator = DiscretionEvaluator(source_name=source, dispatcher=dispatcher)

    with caplog.at_level(logging.ERROR):
        # weight=0.3 is below weight_fail_open (0.5) — fail-closed -> IGNORE.
        result = await evaluator.evaluate(text="ambient chatter", weight=0.3)

    assert result.verdict == "IGNORE"
    assert result.is_fail_open is False
    assert "same_tier_failover_exhausted" in caplog.text

    after = discretion_evaluations_total.labels(
        source=source, verdict="IGNORE", outcome="error"
    )._value.get()
    assert after == before + 1
